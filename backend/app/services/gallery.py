from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.audit import audit_ctx
from app.errors import ServiceError
from app.deps import Ctx, require_operator, require_staff
from app.serialize import dump
from app.services.recognition import decide_match
from app.services.unknowns import attach_unknown
from app.services.vectors import normalize, vec_literal
from app.storage import delete_file, read_encrypted, seal_embedding, sha256, write_encrypted


async def policy(ctx: Ctx) -> dict:
    row = (
        await ctx.session.execute(
            text("SELECT * FROM attendance_policies WHERE org_id = CAST(:org AS uuid)"),
            {"org": ctx.org_id},
        )
    ).mappings().first()
    if not row:
        raise ServiceError(500, "Attendance policy is missing for this organization")
    return dump(dict(row))


async def _next_person_code(ctx: Ctx, person_type: str) -> str:
    prefix = {"student": "STU", "coach": "CCH", "teacher": "TCH", "visitor": "VIS"}[person_type]
    seq = (
        await ctx.session.execute(
            text(
                """
                UPDATE org_counters
                SET person_seq = person_seq + 1
                WHERE org_id = CAST(:org AS uuid)
                RETURNING person_seq
                """
            ),
            {"org": ctx.org_id},
        )
    ).scalar_one()
    return f"{prefix}-{int(seq):04d}"


async def create_person(ctx: Ctx, data: dict) -> dict:
    require_operator(ctx)
    if ctx.user["role"] in {"coach", "teacher"} and data["person_type"] != "student":
        raise ServiceError(403, "Coaches and teachers can add students only")
    code = (data.get("code") or "").strip() or await _next_person_code(ctx, data["person_type"])
    try:
        async with ctx.session.begin_nested():
            row = (
                await ctx.session.execute(
                    text(
                        """
                        INSERT INTO persons (
                          org_id, person_type, code, full_name, dob, phone, email,
                          guardian_name, guardian_phone, guardian_email, sport_specialization,
                          external_id, joining_date, enrollment_status, status
                        )
                        VALUES (
                          CAST(:org AS uuid), :person_type, :code, :full_name, :dob, :phone, :email,
                          :guardian_name, :guardian_phone, :guardian_email, :sport_specialization,
                          :external_id, :joining_date, 'draft', 'active'
                        )
                        RETURNING *
                        """
                    ),
                    {
                        "org": ctx.org_id,
                        "person_type": data["person_type"],
                        "code": code,
                        "full_name": data["full_name"].strip(),
                        "dob": data.get("dob"),
                        "phone": data.get("phone"),
                        "email": (data.get("email") or None),
                        "guardian_name": data.get("guardian_name"),
                        "guardian_phone": data.get("guardian_phone"),
                        "guardian_email": data.get("guardian_email"),
                        "sport_specialization": data.get("sport_specialization"),
                        "external_id": data.get("external_id"),
                        "joining_date": data.get("joining_date"),
                    },
                )
            ).mappings().one()
    except DBAPIError as exc:
        if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
            raise ServiceError(409, "A person with that code or external id already exists") from exc
        raise
    person = dump(dict(row))
    for batch_id in data.get("batch_ids") or []:
        await assign_batch(ctx, person["id"], batch_id, "student" if data["person_type"] == "student" else data["person_type"])
    await audit_ctx(ctx, "person.create", "person", person["id"], after={"code": person["code"], "name": person["full_name"]})
    return await get_person(ctx, person["id"])


async def assign_batch(ctx: Ctx, person_id: str, batch_id: str, role: str) -> None:
    try:
        async with ctx.session.begin_nested():
            await ctx.session.execute(
                text(
                    """
                    INSERT INTO person_batches (org_id, person_id, batch_id, role)
                    VALUES (CAST(:org AS uuid), CAST(:person AS uuid), CAST(:batch AS uuid), :role)
                    ON CONFLICT DO NOTHING
                    """
                ),
                {"org": ctx.org_id, "person": person_id, "batch": batch_id, "role": role},
            )
    except DBAPIError as exc:
        message = str(exc).lower()
        if "does not belong" in message:
            raise ServiceError(400, "That batch is not in this organization") from exc
        raise


async def get_person(ctx: Ctx, person_id: str) -> dict:
    row = (
        await ctx.session.execute(
            text(
                """
                SELECT p.*,
                  COALESCE((
                    SELECT json_agg(json_build_object('id', b.id, 'name', b.name, 'role', pb.role))
                    FROM person_batches pb
                    JOIN batches b ON b.id = pb.batch_id
                    WHERE pb.person_id = p.id
                  ), '[]'::json) AS batches,
                  (SELECT count(*) FROM face_embeddings e WHERE e.person_id = p.id) AS embedding_count,
                  (SELECT count(*) FROM face_images i WHERE i.person_id = p.id) AS photo_count
                FROM persons p
                WHERE p.id = CAST(:id AS uuid)
                """
            ),
            {"id": person_id},
        )
    ).mappings().first()
    if not row:
        raise ServiceError(404, "Person not found")
    person = dump(dict(row))
    if ctx.user["role"] == "student":
        if person.get("user_id") != ctx.user["id"]:
            raise ServiceError(404, "Person not found")
    return person


async def add_photos(ctx: Ctx, person_id: str, files: list[tuple[str, bytes]], face) -> dict:
    require_operator(ctx)
    person = await get_person(ctx, person_id)
    if person["biometrics_deleted_at"]:
        raise ServiceError(400, "Biometrics were deleted for this person. Create a new enrollment.")
    if len(files) > 8:
        raise ServiceError(400, "Upload at most 8 photos at a time")
    accepted = []
    rejected = []
    for name, blob in files:
        if len(blob) > 8_000_000:
            rejected.append({"file": name, "reason": "file is larger than 8MB"})
            continue
        analysis = await face.analyze(blob)
        faces = analysis.get("faces") or []
        if len(faces) != 1:
            rejected.append({"file": name, "reason": "no_face" if not faces else "multiple_faces"})
            continue
        found = faces[0]
        if not found.get("acceptable", False):
            rejected.append({"file": name, "reason": "low_quality", "quality": found.get("quality"), "notes": found.get("notes")})
            continue
        if not found.get("live", False):
            rejected.append({"file": name, "reason": "failed_liveness", "liveness": found.get("liveness")})
            continue
        embedding = normalize([float(value) for value in found["embedding"]])
        if len(embedding) != 128:
            rejected.append({"file": name, "reason": "bad_embedding"})
            continue
        image_row = (
            await ctx.session.execute(
                text(
                    """
                    INSERT INTO face_images (
                      org_id, person_id, storage_path, sha256, width, height,
                      quality_score, liveness_score, liveness_label
                    )
                    VALUES (
                      CAST(:org AS uuid), CAST(:person AS uuid), '', :sha, :width, :height,
                      :quality, :liveness, :label
                    )
                    RETURNING id
                    """
                ),
                {
                    "org": ctx.org_id,
                    "person": person_id,
                    "sha": sha256(blob),
                    "width": found.get("width"),
                    "height": found.get("height"),
                    "quality": found.get("quality"),
                    "liveness": found.get("liveness"),
                    "label": "real" if found.get("live") else "fake",
                },
            )
        ).scalar_one()
        image_id = str(image_row)
        folder = "students" if person["person_type"] == "student" else "coaches" if person["person_type"] == "coach" else "face_images"
        relative = f"organizations/{ctx.org_code}/face_images/{person_id}/{image_id}.bin"
        write_encrypted(relative, blob)
        # Keep a copy pointer in the role folder so the on-disk layout matches the spec.
        role_copy = f"organizations/{ctx.org_code}/{folder}/{person_id}/{image_id}.bin"
        write_encrypted(role_copy, blob)
        await ctx.session.execute(
            text("UPDATE face_images SET storage_path = :path WHERE id = CAST(:id AS uuid)"),
            {"path": relative, "id": image_id},
        )
        await ctx.session.execute(
            text(
                """
                INSERT INTO face_embeddings (
                  org_id, person_id, face_image_id, embedding, embedding_sealed, model_name
                )
                VALUES (
                  CAST(:org AS uuid), CAST(:person AS uuid), CAST(:image AS uuid),
                  CAST(:embedding AS vector), :sealed, :model
                )
                """
            ),
            {
                "org": ctx.org_id,
                "person": person_id,
                "image": image_id,
                "embedding": vec_literal(embedding),
                "sealed": seal_embedding(embedding),
                "model": found.get("model") or "sface_2021dec",
            },
        )
        if not person.get("photo_path"):
            await ctx.session.execute(
                text("UPDATE persons SET photo_path = :path, enrollment_status = 'pending_review' WHERE id = CAST(:id AS uuid) AND enrollment_status = 'draft'"),
                {"path": relative, "id": person_id},
            )
        else:
            await ctx.session.execute(
                text(
                    """
                    UPDATE persons
                    SET enrollment_status = 'pending_review'
                    WHERE id = CAST(:id AS uuid) AND enrollment_status = 'draft'
                    """
                ),
                {"id": person_id},
            )
        accepted.append({"image_id": image_id, "quality": found.get("quality"), "liveness": found.get("liveness")})
    await audit_ctx(
        ctx,
        "person.photos",
        "person",
        person_id,
        after={"accepted": len(accepted), "rejected": len(rejected)},
    )
    return {"accepted": accepted, "rejected": rejected, "person": await get_person(ctx, person_id)}


async def confirm_enrollment(ctx: Ctx, person_id: str) -> dict:
    require_staff(ctx)
    person = await get_person(ctx, person_id)
    if int(person["embedding_count"]) < 1:
        raise ServiceError(400, "Upload at least one accepted photo before confirming enrollment")
    await ctx.session.execute(
        text("UPDATE persons SET enrollment_status = 'enrolled' WHERE id = CAST(:id AS uuid)"),
        {"id": person_id},
    )
    await audit_ctx(ctx, "person.enroll", "person", person_id, before={"enrollment_status": person["enrollment_status"]}, after={"enrollment_status": "enrolled"})
    return await get_person(ctx, person_id)


async def search_gallery(ctx: Ctx, embedding: list[float]) -> list[tuple[str, str, str, float]]:
    rows = (
        await ctx.session.execute(
            text(
                """
                SELECT p.id AS person_id, p.full_name, p.code,
                       MAX(1 - (e.embedding <=> CAST(:vec AS vector)))::float8 AS similarity
                FROM face_embeddings e
                JOIN persons p ON p.id = e.person_id
                WHERE p.enrollment_status = 'enrolled' AND p.status = 'active'
                GROUP BY p.id, p.full_name, p.code
                ORDER BY similarity DESC
                LIMIT 5
                """
            ),
            {"vec": vec_literal(embedding)},
        )
    ).mappings().all()
    return [(str(row["person_id"]), row["full_name"], row["code"], float(row["similarity"])) for row in rows]


def _empty_identity() -> dict:
    return {
        "result": "no_face",
        "confidence": None,
        "person_id": None,
        "person_name": None,
        "person_code": None,
        "reason": None,
        "unknown_group_id": None,
        "unknown_code": None,
    }


async def identify_image(
    ctx: Ctx,
    image: bytes,
    face,
    *,
    camera_id: str | None = None,
    persist_unknown: bool = True,
    analysis: dict | None = None,
) -> dict:
    rules = await policy(ctx)
    if analysis is None:
        analysis = await face.analyze(image)
    faces = analysis.get("faces") or []
    if not faces:
        return _empty_identity()
    if len(faces) > 1:
        results = []
        for found in faces:
            results.append(
                await _identify_one(ctx, image, found, rules, camera_id=camera_id, persist_unknown=persist_unknown)
            )
        return {**_empty_identity(), "result": "multiple", "faces": results, "face_count": len(faces)}
    result = await _identify_one(ctx, image, faces[0], rules, camera_id=camera_id, persist_unknown=persist_unknown)
    result["face_count"] = 1
    return result


async def _identify_one(ctx, image, found, rules, *, camera_id, persist_unknown) -> dict:
    base = _empty_identity()
    if not found.get("acceptable", False):
        base["result"] = "low_quality"
        base["confidence"] = None
        base["reason"] = "low_quality"
        base["quality"] = found.get("quality")
        return base
    if not found.get("live", False):
        base["result"] = "failed_liveness"
        base["reason"] = "failed_liveness"
        base["liveness"] = found.get("liveness")
        return base
    embedding = normalize([float(value) for value in found["embedding"]])
    ranked = await search_gallery(ctx, embedding)
    decision = decide_match(ranked, float(rules["match_threshold"]), float(rules["ambiguity_margin"]))
    payload = decision.as_dict()
    payload["unknown_group_id"] = None
    payload["unknown_code"] = None
    if decision.result != "known" and persist_unknown:
        group = await attach_unknown(
            ctx,
            embedding=embedding,
            image=image,
            confidence=decision.confidence or 0.0,
            camera_id=camera_id,
            group_threshold=float(rules["unknown_group_threshold"]),
        )
        payload["unknown_group_id"] = group["id"]
        payload["unknown_code"] = group["code"]
        payload["person_id"] = None
        payload["person_name"] = None
        payload["person_code"] = None
    return payload


async def delete_biometrics(ctx: Ctx, person_id: str) -> dict:
    require_operator(ctx)
    person = await get_person(ctx, person_id)
    rows = (
        await ctx.session.execute(
            text("SELECT id, storage_path FROM face_images WHERE person_id = CAST(:id AS uuid)"),
            {"id": person_id},
        )
    ).mappings().all()
    embedding_count = (
        await ctx.session.execute(
            text("SELECT count(*) FROM face_embeddings WHERE person_id = CAST(:id AS uuid)"),
            {"id": person_id},
        )
    ).scalar_one()
    for row in rows:
        delete_file(row["storage_path"])
    await ctx.session.execute(
        text("DELETE FROM face_embeddings WHERE person_id = CAST(:id AS uuid)"),
        {"id": person_id},
    )
    await ctx.session.execute(
        text("DELETE FROM face_images WHERE person_id = CAST(:id AS uuid)"),
        {"id": person_id},
    )
    await ctx.session.execute(
        text(
            """
            UPDATE persons
            SET photo_path = NULL,
                enrollment_status = 'draft',
                biometrics_deleted_at = now()
            WHERE id = CAST(:id AS uuid)
            """
        ),
        {"id": person_id},
    )
    await audit_ctx(
        ctx,
        "person.biometrics_delete",
        "person",
        person_id,
        before={"code": person["code"]},
        after={"embeddings_removed": int(embedding_count), "images_removed": len(rows)},
    )
    return {
        "id": person_id,
        "biometrics_deleted": True,
        "embeddings_removed": int(embedding_count),
        "images_removed": len(rows),
    }


def read_photo(relative_path: str) -> bytes:
    return read_encrypted(relative_path)
