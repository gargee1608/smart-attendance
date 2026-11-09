from datetime import datetime, timezone

from sqlalchemy import text

from app.audit import audit_ctx
from app.errors import ServiceError
from app.deps import Ctx, require_operator, require_staff
from app.serialize import dump
from app.services.vectors import cosine, mean_normalize, normalize, parse_vec, vec_literal
from app.storage import delete_file, seal_embedding, sha256, write_encrypted


def dbscan(vectors: list[list[float]], eps: float, min_samples: int = 2) -> list[int]:
    """Cosine-distance DBSCAN. Labels are cluster ids; noise is -1."""
    count = len(vectors)
    labels = [-1] * count
    visited = [False] * count

    def distance(i: int, j: int) -> float:
        return 1.0 - cosine(vectors[i], vectors[j])

    def region(index: int) -> list[int]:
        return [other for other in range(count) if distance(index, other) <= eps]

    cluster = 0
    for index in range(count):
        if visited[index]:
            continue
        visited[index] = True
        neighbors = region(index)
        if len(neighbors) < min_samples:
            continue
        labels[index] = cluster
        seeds = list(neighbors)
        cursor = 0
        while cursor < len(seeds):
            point = seeds[cursor]
            cursor += 1
            if not visited[point]:
                visited[point] = True
                more = region(point)
                if len(more) >= min_samples:
                    for candidate in more:
                        if candidate not in seeds:
                            seeds.append(candidate)
            if labels[point] < 0:
                labels[point] = cluster
        cluster += 1
    return labels


async def _next_unknown_code(ctx: Ctx) -> str:
    seq = (
        await ctx.session.execute(
            text(
                """
                UPDATE org_counters
                SET unknown_seq = unknown_seq + 1
                WHERE org_id = CAST(:org AS uuid)
                RETURNING unknown_seq
                """
            ),
            {"org": ctx.org_id},
        )
    ).scalar_one()
    return f"UNK-{int(seq):04d}"


async def attach_unknown(
    ctx: Ctx,
    *,
    embedding: list[float],
    image: bytes,
    confidence: float,
    camera_id: str | None,
    group_threshold: float,
) -> dict:
    embedding = normalize(embedding)
    match = (
        await ctx.session.execute(
            text(
                """
                SELECT id, code, observation_count, centroid
                FROM unknown_groups
                WHERE status = 'open' AND centroid IS NOT NULL
                ORDER BY centroid <=> CAST(:vec AS vector)
                LIMIT 1
                """
            ),
            {"vec": vec_literal(embedding)},
        )
    ).mappings().first()
    group_id = None
    code = None
    created = False
    if match:
        centroid = parse_vec(match["centroid"])
        similarity = cosine(normalize(centroid), embedding) if centroid else -1
        if similarity >= group_threshold:
            group_id = str(match["id"])
            code = match["code"]
            updated = mean_normalize(normalize(centroid), int(match["observation_count"]), embedding)
            await ctx.session.execute(
                text(
                    """
                    UPDATE unknown_groups
                    SET centroid = CAST(:vec AS vector),
                        observation_count = observation_count + 1,
                        last_seen = now()
                    WHERE id = CAST(:id AS uuid)
                    """
                ),
                {"vec": vec_literal(updated), "id": group_id},
            )
    if group_id is None:
        code = await _next_unknown_code(ctx)
        group_id = str(
            (
                await ctx.session.execute(
                    text(
                        """
                        INSERT INTO unknown_groups (
                          org_id, code, status, centroid, observation_count, first_seen, last_seen
                        )
                        VALUES (
                          CAST(:org AS uuid), :code, 'open', CAST(:vec AS vector), 1, now(), now()
                        )
                        RETURNING id
                        """
                    ),
                    {"org": ctx.org_id, "code": code, "vec": vec_literal(embedding)},
                )
            ).scalar_one()
        )
        created = True
        await ctx.session.execute(
            text(
                """
                INSERT INTO notifications (org_id, kind, message, entity_type, entity_id)
                VALUES (
                  CAST(:org AS uuid), 'unknown_face', :message, 'unknown_group', :entity
                )
                """
            ),
            {
                "org": ctx.org_id,
                "message": f"Unrecognized face {code} is waiting for review. This is not an identification.",
                "entity": group_id,
            },
        )
    image_id = str(
        (
            await ctx.session.execute(
                text(
                    """
                    INSERT INTO face_images (org_id, storage_path, sha256, liveness_label)
                    VALUES (CAST(:org AS uuid), '', :sha, 'real')
                    RETURNING id
                    """
                ),
                {"org": ctx.org_id, "sha": sha256(image)},
            )
        ).scalar_one()
    )
    relative = f"organizations/{ctx.org_code}/unknown_faces/{group_id}/{image_id}.bin"
    write_encrypted(relative, image)
    await ctx.session.execute(
        text("UPDATE face_images SET storage_path = :path WHERE id = CAST(:id AS uuid)"),
        {"path": relative, "id": image_id},
    )
    await ctx.session.execute(
        text(
            """
            INSERT INTO unknown_observations (
              org_id, group_id, camera_id, face_image_id, embedding, embedding_sealed, confidence
            )
            VALUES (
              CAST(:org AS uuid), CAST(:group AS uuid), CAST(:camera AS uuid), CAST(:image AS uuid),
              CAST(:vec AS vector), :sealed, :confidence
            )
            """
        ),
        {
            "org": ctx.org_id,
            "group": group_id,
            "camera": camera_id,
            "image": image_id,
            "vec": vec_literal(embedding),
            "sealed": seal_embedding(embedding),
            "confidence": confidence,
        },
    )
    if created:
        await audit_ctx(ctx, "unknown.create", "unknown_group", group_id, after={"code": code})
    return {"id": group_id, "code": code}


async def list_unknowns(ctx: Ctx, status: str | None = None) -> list[dict]:
    sql = """
      SELECT g.*,
        (
          SELECT i.storage_path FROM unknown_observations o
          JOIN face_images i ON i.id = o.face_image_id
          WHERE o.group_id = g.id
          ORDER BY o.captured_at DESC LIMIT 1
        ) AS latest_snapshot,
        (
          SELECT c.name FROM unknown_observations o
          LEFT JOIN cameras c ON c.id = o.camera_id
          WHERE o.group_id = g.id
          ORDER BY o.captured_at DESC LIMIT 1
        ) AS latest_camera
      FROM unknown_groups g
    """
    params: dict = {}
    if status:
        sql += " WHERE g.status = :status"
        params["status"] = status
    sql += " ORDER BY g.last_seen DESC NULLS LAST"
    rows = (await ctx.session.execute(text(sql), params)).mappings().all()
    items = []
    for row in rows:
        item = dump(dict(row))
        item.pop("centroid", None)
        items.append(item)
    return items


async def resolve_unknown(ctx: Ctx, group_id: str, body: dict) -> dict:
    require_operator(ctx)
    group = (
        await ctx.session.execute(
            text("SELECT * FROM unknown_groups WHERE id = CAST(:id AS uuid)"),
            {"id": group_id},
        )
    ).mappings().first()
    if not group:
        raise ServiceError(404, "Unknown face group not found")
    action = body["action"]
    note = body.get("note")
    person_id = body.get("person_id")
    if action == "assign":
        if not person_id:
            raise ServiceError(400, "Choose an existing person")
        exists = (
            await ctx.session.execute(
                text("SELECT id FROM persons WHERE id = CAST(:id AS uuid)"),
                {"id": person_id},
            )
        ).first()
        if not exists:
            raise ServiceError(404, "Person not found")
        # Assignment records the decision. It does not copy embeddings into the gallery.
        await _close(ctx, group_id, "assigned", person_id, note)
        await audit_ctx(ctx, "unknown.assign", "unknown_group", group_id, after={"person_id": person_id, "trained": False})
    elif action == "register":
        from app.services.gallery import create_person

        person = await create_person(
            ctx,
            {
                "person_type": body.get("person_type") or "student",
                "full_name": body.get("full_name") or f"Review {group['code']}",
                "phone": body.get("phone"),
                "email": body.get("email"),
            },
        )
        if body.get("use_observations_as_enrollment"):
            await _copy_observations(ctx, group_id, person["id"])
        await _close(ctx, group_id, "registered", person["id"], note)
        await audit_ctx(
            ctx,
            "unknown.register",
            "unknown_group",
            group_id,
            after={"person_id": person["id"], "copied": bool(body.get("use_observations_as_enrollment")), "enrolled": False},
        )
    elif action == "visitor":
        from app.services.gallery import create_person

        person = await create_person(
            ctx,
            {"person_type": "visitor", "full_name": body.get("full_name") or f"Visitor {group['code']}"},
        )
        await _close(ctx, group_id, "visitor", person["id"], note)
        await audit_ctx(ctx, "unknown.visitor", "unknown_group", group_id, after={"person_id": person["id"]})
    elif action == "dismiss":
        await _close(ctx, group_id, "dismissed", None, note)
        await audit_ctx(ctx, "unknown.dismiss", "unknown_group", group_id, after={"note": note})
    elif action == "delete":
        await _delete_group(ctx, group_id)
        await audit_ctx(ctx, "unknown.delete", "unknown_group", group_id, before={"code": group["code"]})
        return {"id": group_id, "status": "deleted"}
    else:
        raise ServiceError(400, "Unknown action")
    rows = await list_unknowns(ctx)
    for item in rows:
        if item["id"] == group_id:
            return item
    fresh = (
        await ctx.session.execute(
            text("SELECT id, code, status, resolved_person_id, note FROM unknown_groups WHERE id = CAST(:id AS uuid)"),
            {"id": group_id},
        )
    ).mappings().first()
    return dump(dict(fresh)) if fresh else {"id": group_id, "status": action}


async def _close(ctx, group_id, status, person_id, note) -> None:
    await ctx.session.execute(
        text(
            """
            UPDATE unknown_groups
            SET status = :status,
                resolved_person_id = CAST(:person AS uuid),
                resolved_by = CAST(:actor AS uuid),
                resolved_at = :now,
                note = :note
            WHERE id = CAST(:id AS uuid)
            """
        ),
        {
            "status": status,
            "person": person_id,
            "actor": ctx.user["id"] if ctx.user["role"] != "api_key" else None,
            "now": datetime.now(timezone.utc),
            "note": note,
            "id": group_id,
        },
    )


async def _copy_observations(ctx, group_id: str, person_id: str) -> None:
    rows = (
        await ctx.session.execute(
            text(
                """
                SELECT o.embedding, o.embedding_sealed, i.storage_path, i.sha256
                FROM unknown_observations o
                JOIN face_images i ON i.id = o.face_image_id
                WHERE o.group_id = CAST(:id AS uuid)
                """
            ),
            {"id": group_id},
        )
    ).mappings().all()
    for row in rows:
        image_id = str(
            (
                await ctx.session.execute(
                    text(
                        """
                        INSERT INTO face_images (org_id, person_id, storage_path, sha256, liveness_label)
                        VALUES (CAST(:org AS uuid), CAST(:person AS uuid), :path, :sha, 'real')
                        RETURNING id
                        """
                    ),
                    {
                        "org": ctx.org_id,
                        "person": person_id,
                        "path": row["storage_path"],
                        "sha": row["sha256"],
                    },
                )
            ).scalar_one()
        )
        await ctx.session.execute(
            text(
                """
                INSERT INTO face_embeddings (
                  org_id, person_id, face_image_id, embedding, embedding_sealed, model_name
                )
                VALUES (
                  CAST(:org AS uuid), CAST(:person AS uuid), CAST(:image AS uuid),
                  CAST(:vec AS vector), :sealed, 'sface_2021dec'
                )
                """
            ),
            {
                "org": ctx.org_id,
                "person": person_id,
                "image": image_id,
                "vec": str(row["embedding"]),
                "sealed": row["embedding_sealed"],
            },
        )
    await ctx.session.execute(
        text(
            """
            UPDATE persons SET enrollment_status = 'pending_review'
            WHERE id = CAST(:id AS uuid) AND enrollment_status = 'draft'
            """
        ),
        {"id": person_id},
    )


async def _delete_group(ctx, group_id: str) -> None:
    paths = (
        await ctx.session.execute(
            text(
                """
                SELECT i.storage_path
                FROM unknown_observations o
                JOIN face_images i ON i.id = o.face_image_id
                WHERE o.group_id = CAST(:id AS uuid)
                """
            ),
            {"id": group_id},
        )
    ).scalars().all()
    for path in paths:
        delete_file(path)
    await ctx.session.execute(
        text(
            """
            DELETE FROM face_images
            WHERE id IN (SELECT face_image_id FROM unknown_observations WHERE group_id = CAST(:id AS uuid))
            """
        ),
        {"id": group_id},
    )
    await ctx.session.execute(text("DELETE FROM unknown_groups WHERE id = CAST(:id AS uuid)"), {"id": group_id})


async def regroup_dbscan(ctx: Ctx) -> dict:
    require_staff(ctx)
    rules = (
        await ctx.session.execute(
            text("SELECT unknown_group_threshold FROM attendance_policies WHERE org_id = CAST(:org AS uuid)"),
            {"org": ctx.org_id},
        )
    ).mappings().first()
    threshold = float(rules["unknown_group_threshold"]) if rules else 0.55
    rows = (
        await ctx.session.execute(
            text(
                """
                SELECT o.id, o.group_id, o.embedding
                FROM unknown_observations o
                JOIN unknown_groups g ON g.id = o.group_id
                WHERE g.status = 'open' AND o.embedding IS NOT NULL
                """
            )
        )
    ).mappings().all()
    if len(rows) < 2:
        return {"observations": len(rows), "clusters": 0, "merged": 0}
    vectors = [normalize(parse_vec(row["embedding"])) for row in rows]
    labels = dbscan(vectors, eps=1.0 - threshold, min_samples=2)
    clusters: dict[int, list] = {}
    for row, label in zip(rows, labels):
        if label < 0:
            continue
        clusters.setdefault(label, []).append(row)
    merged = 0
    for members in clusters.values():
        group_ids = [str(item["group_id"]) for item in members]
        keep = group_ids[0]
        for other in set(group_ids):
            if other == keep:
                continue
            await ctx.session.execute(
                text("UPDATE unknown_observations SET group_id = CAST(:keep AS uuid) WHERE group_id = CAST(:other AS uuid)"),
                {"keep": keep, "other": other},
            )
            await ctx.session.execute(text("DELETE FROM unknown_groups WHERE id = CAST(:id AS uuid)"), {"id": other})
            merged += 1
        obs = (
            await ctx.session.execute(
                text("SELECT embedding FROM unknown_observations WHERE group_id = CAST(:id AS uuid)"),
                {"id": keep},
            )
        ).scalars().all()
        parsed = [normalize(parse_vec(item)) for item in obs if item is not None]
        if not parsed:
            continue
        centroid = parsed[0]
        for extra in parsed[1:]:
            centroid = mean_normalize(centroid, 1, extra)
        await ctx.session.execute(
            text(
                """
                UPDATE unknown_groups
                SET centroid = CAST(:vec AS vector), observation_count = :count
                WHERE id = CAST(:id AS uuid)
                """
            ),
            {"vec": vec_literal(normalize(centroid)), "count": len(parsed), "id": keep},
        )
    await audit_ctx(ctx, "unknown.regroup", "unknown_group", None, after={"merged": merged})
    return {"observations": len(rows), "clusters": len(clusters), "merged": merged}
