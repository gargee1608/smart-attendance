-- Smart Attendance v1 schema.
-- Applied by Alembic as the database superuser. Request traffic uses
-- smartattendance_app (row-level security) or smartattendance_admin
-- (BYPASSRLS, super-admin platform routes only).

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'smartattendance_app') THEN
    CREATE ROLE smartattendance_app LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'smartattendance_admin') THEN
    CREATE ROLE smartattendance_admin LOGIN NOSUPERUSER BYPASSRLS NOCREATEDB NOCREATEROLE;
  END IF;
END $$;

ALTER ROLE smartattendance_app NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
ALTER ROLE smartattendance_admin NOSUPERUSER BYPASSRLS NOCREATEDB NOCREATEROLE;
ALTER ROLE smartattendance_app SET statement_timeout = '30s';
ALTER ROLE smartattendance_admin SET statement_timeout = '60s';
ALTER ROLE smartattendance_app SET row_security = on;
ALTER ROLE smartattendance_admin SET row_security = on;

CREATE SEQUENCE IF NOT EXISTS org_code_seq START 1;

CREATE OR REPLACE FUNCTION app_current_org() RETURNS uuid
LANGUAGE plpgsql STABLE AS $$
DECLARE
  raw text;
BEGIN
  raw := current_setting('app.current_org_id', true);
  IF raw IS NULL OR btrim(raw) = '' THEN
    RETURN NULL;
  END IF;
  RETURN raw::uuid;
EXCEPTION WHEN invalid_text_representation THEN
  RETURN NULL;
END $$;

CREATE TABLE organizations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  code text UNIQUE NOT NULL,
  name text NOT NULL,
  org_type text NOT NULL CHECK (org_type IN (
    'sports_academy', 'school', 'college', 'coaching_institute', 'private_org'
  )),
  logo_path text,
  address text,
  contact_person text,
  email text,
  phone text,
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended')),
  settings jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid REFERENCES organizations(id) ON DELETE CASCADE,
  email text NOT NULL,
  password_hash text NOT NULL,
  full_name text NOT NULL,
  role text NOT NULL CHECK (role IN (
    'super_admin', 'org_admin', 'coach', 'teacher', 'student', 'parent'
  )),
  phone text,
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended')),
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT users_super_admin_org CHECK (
    (role = 'super_admin' AND org_id IS NULL) OR (role <> 'super_admin' AND org_id IS NOT NULL)
  )
);

CREATE UNIQUE INDEX users_email_unique ON users (lower(email));

CREATE TABLE org_counters (
  org_id uuid PRIMARY KEY REFERENCES organizations(id) ON DELETE CASCADE,
  person_seq integer NOT NULL DEFAULT 0,
  unknown_seq integer NOT NULL DEFAULT 0
);

CREATE TABLE api_keys (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  name text NOT NULL,
  key_prefix text NOT NULL,
  key_hash text NOT NULL UNIQUE,
  rate_limit_per_minute integer NOT NULL DEFAULT 60 CHECK (rate_limit_per_minute > 0),
  created_by uuid,
  revoked_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE sports (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  name text NOT NULL,
  description text,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, name)
);

CREATE TABLE academic_sessions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  name text NOT NULL,
  start_date date,
  end_date date,
  is_current boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, name)
);

CREATE TABLE school_classes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  session_id uuid REFERENCES academic_sessions(id) ON DELETE SET NULL,
  name text NOT NULL,
  section text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, session_id, name, section)
);

CREATE TABLE subjects (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  name text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, name)
);

CREATE TABLE batches (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  name text NOT NULL,
  sport_id uuid REFERENCES sports(id) ON DELETE SET NULL,
  class_id uuid REFERENCES school_classes(id) ON DELETE SET NULL,
  schedule jsonb NOT NULL DEFAULT '{"weekdays":[0,1,2,3,4],"start_time":"07:00","late_after_minutes":15}'::jsonb,
  start_date date,
  end_date date,
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, name)
);

CREATE TABLE persons (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  person_type text NOT NULL CHECK (person_type IN ('student', 'coach', 'teacher', 'visitor')),
  code text NOT NULL,
  full_name text NOT NULL,
  dob date,
  phone text,
  email text,
  photo_path text,
  guardian_name text,
  guardian_phone text,
  guardian_email text,
  sport_specialization text,
  external_id text,
  user_id uuid REFERENCES users(id) ON DELETE SET NULL,
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive')),
  enrollment_status text NOT NULL DEFAULT 'draft' CHECK (
    enrollment_status IN ('draft', 'pending_review', 'enrolled', 'rejected')
  ),
  joining_date date,
  biometrics_deleted_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, code)
);

CREATE INDEX persons_org_type_idx ON persons (org_id, person_type);
CREATE INDEX persons_external_idx ON persons (org_id, external_id);

CREATE TABLE person_batches (
  org_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  person_id uuid NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
  batch_id uuid NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
  role text NOT NULL CHECK (role IN ('student', 'coach', 'teacher')),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (person_id, batch_id, role)
);

CREATE TABLE class_subject_teachers (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  class_id uuid NOT NULL REFERENCES school_classes(id) ON DELETE CASCADE,
  subject_id uuid NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
  teacher_id uuid NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
  UNIQUE (class_id, subject_id, teacher_id)
);

CREATE TABLE cameras (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  name text NOT NULL,
  rtsp_url text,
  location text,
  batch_id uuid REFERENCES batches(id) ON DELETE SET NULL,
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE attendance_policies (
  org_id uuid PRIMARY KEY REFERENCES organizations(id) ON DELETE CASCADE,
  count_late_as_present boolean NOT NULL DEFAULT true,
  exclude_holidays boolean NOT NULL DEFAULT true,
  exclude_approved_leave boolean NOT NULL DEFAULT true,
  late_after_minutes integer NOT NULL DEFAULT 15,
  match_threshold double precision NOT NULL DEFAULT 0.363,
  ambiguity_margin double precision NOT NULL DEFAULT 0.05,
  unknown_group_threshold double precision NOT NULL DEFAULT 0.55
);

CREATE TABLE holidays (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  holiday_date date NOT NULL,
  name text NOT NULL,
  UNIQUE (org_id, holiday_date)
);

CREATE TABLE leave_requests (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  person_id uuid NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
  start_date date NOT NULL,
  end_date date NOT NULL,
  status text NOT NULL DEFAULT 'approved' CHECK (status IN ('pending', 'approved', 'rejected')),
  reason text,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (end_date >= start_date)
);

CREATE TABLE attendance_records (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  person_id uuid NOT NULL REFERENCES persons(id) ON DELETE RESTRICT,
  batch_id uuid REFERENCES batches(id) ON DELETE SET NULL,
  camera_id uuid REFERENCES cameras(id) ON DELETE SET NULL,
  attendance_date date NOT NULL,
  status text NOT NULL CHECK (status IN ('present', 'absent', 'late', 'leave', 'holiday', 'manual')),
  method text NOT NULL CHECK (method IN ('face', 'manual', 'correction', 'import')),
  confidence double precision,
  check_in_at timestamptz,
  note text,
  created_by uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX attendance_dedup ON attendance_records (
  org_id, person_id, COALESCE(batch_id, '00000000-0000-0000-0000-000000000000'::uuid), attendance_date
);
CREATE INDEX attendance_org_date_idx ON attendance_records (org_id, attendance_date);

CREATE TABLE face_images (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  person_id uuid REFERENCES persons(id) ON DELETE CASCADE,
  storage_path text NOT NULL,
  sha256 text NOT NULL,
  width integer,
  height integer,
  quality_score double precision,
  liveness_score double precision,
  liveness_label text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE face_embeddings (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  person_id uuid NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
  face_image_id uuid NOT NULL REFERENCES face_images(id) ON DELETE CASCADE,
  embedding vector(128) NOT NULL,
  embedding_sealed bytea NOT NULL,
  model_name text NOT NULL DEFAULT 'sface_2021dec',
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX face_embeddings_org_idx ON face_embeddings (org_id);
CREATE INDEX face_embeddings_hnsw ON face_embeddings USING hnsw (embedding vector_cosine_ops);

CREATE TABLE unknown_groups (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  code text NOT NULL,
  status text NOT NULL DEFAULT 'open' CHECK (
    status IN ('open', 'assigned', 'registered', 'visitor', 'dismissed')
  ),
  centroid vector(128),
  observation_count integer NOT NULL DEFAULT 0,
  first_seen timestamptz,
  last_seen timestamptz,
  resolved_person_id uuid REFERENCES persons(id) ON DELETE SET NULL,
  resolved_by uuid,
  resolved_at timestamptz,
  note text,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, code)
);

CREATE TABLE unknown_observations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  group_id uuid NOT NULL REFERENCES unknown_groups(id) ON DELETE CASCADE,
  camera_id uuid REFERENCES cameras(id) ON DELETE SET NULL,
  face_image_id uuid REFERENCES face_images(id) ON DELETE SET NULL,
  embedding vector(128),
  embedding_sealed bytea,
  confidence double precision,
  captured_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX unknown_obs_group_idx ON unknown_observations (group_id, captured_at);

CREATE TABLE audit_logs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid REFERENCES organizations(id) ON DELETE CASCADE,
  actor_id uuid,
  actor_role text,
  action text NOT NULL,
  entity_type text NOT NULL,
  entity_id text,
  before_data jsonb,
  after_data jsonb,
  ip_address text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX audit_org_idx ON audit_logs (org_id, created_at DESC);

CREATE TABLE notifications (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  kind text NOT NULL,
  message text NOT NULL,
  entity_type text,
  entity_id text,
  read_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE performance_metrics (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  person_id uuid NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
  sport_id uuid REFERENCES sports(id) ON DELETE SET NULL,
  period_start date,
  period_end date,
  metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_by uuid,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE coach_comments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  person_id uuid NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
  coach_id uuid REFERENCES persons(id) ON DELETE SET NULL,
  sport_id uuid REFERENCES sports(id) ON DELETE SET NULL,
  period text,
  comment text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE ai_comments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  person_id uuid NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
  suggestion text NOT NULL,
  status text NOT NULL DEFAULT 'suggestion' CHECK (status IN ('suggestion', 'approved', 'rejected')),
  model text,
  created_at timestamptz NOT NULL DEFAULT now(),
  reviewed_by uuid,
  reviewed_at timestamptz
);

CREATE TABLE import_jobs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  filename text,
  status text NOT NULL,
  report jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_by uuid,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION app_assert_same_org() RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  payload jsonb := to_jsonb(NEW);
  ref_id uuid;
  ref_org uuid;
  key text;
BEGIN
  FOREACH key IN ARRAY ARRAY['person_id', 'teacher_id', 'coach_id', 'resolved_person_id']
  LOOP
    IF payload ? key AND NULLIF(payload->>key, '') IS NOT NULL THEN
      ref_id := (payload->>key)::uuid;
      SELECT org_id INTO ref_org FROM persons WHERE id = ref_id;
      IF ref_org IS NULL OR ref_org <> NEW.org_id THEN
        RAISE EXCEPTION 'person does not belong to this organization';
      END IF;
    END IF;
  END LOOP;
  IF payload ? 'batch_id' AND NULLIF(payload->>'batch_id', '') IS NOT NULL THEN
    ref_id := (payload->>'batch_id')::uuid;
    SELECT org_id INTO ref_org FROM batches WHERE id = ref_id;
    IF ref_org IS NULL OR ref_org <> NEW.org_id THEN
      RAISE EXCEPTION 'batch does not belong to this organization';
    END IF;
  END IF;
  IF payload ? 'class_id' AND NULLIF(payload->>'class_id', '') IS NOT NULL THEN
    ref_id := (payload->>'class_id')::uuid;
    SELECT org_id INTO ref_org FROM school_classes WHERE id = ref_id;
    IF ref_org IS NULL OR ref_org <> NEW.org_id THEN
      RAISE EXCEPTION 'class does not belong to this organization';
    END IF;
  END IF;
  IF payload ? 'subject_id' AND NULLIF(payload->>'subject_id', '') IS NOT NULL THEN
    ref_id := (payload->>'subject_id')::uuid;
    SELECT org_id INTO ref_org FROM subjects WHERE id = ref_id;
    IF ref_org IS NULL OR ref_org <> NEW.org_id THEN
      RAISE EXCEPTION 'subject does not belong to this organization';
    END IF;
  END IF;
  IF payload ? 'sport_id' AND NULLIF(payload->>'sport_id', '') IS NOT NULL THEN
    ref_id := (payload->>'sport_id')::uuid;
    SELECT org_id INTO ref_org FROM sports WHERE id = ref_id;
    IF ref_org IS NULL OR ref_org <> NEW.org_id THEN
      RAISE EXCEPTION 'sport does not belong to this organization';
    END IF;
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_same_org_batches BEFORE INSERT OR UPDATE ON batches
  FOR EACH ROW EXECUTE FUNCTION app_assert_same_org();
CREATE TRIGGER trg_same_org_attendance BEFORE INSERT OR UPDATE ON attendance_records
  FOR EACH ROW EXECUTE FUNCTION app_assert_same_org();
CREATE TRIGGER trg_same_org_face_images BEFORE INSERT OR UPDATE ON face_images
  FOR EACH ROW EXECUTE FUNCTION app_assert_same_org();
CREATE TRIGGER trg_same_org_face_embeddings BEFORE INSERT OR UPDATE ON face_embeddings
  FOR EACH ROW EXECUTE FUNCTION app_assert_same_org();
CREATE TRIGGER trg_same_org_leave BEFORE INSERT OR UPDATE ON leave_requests
  FOR EACH ROW EXECUTE FUNCTION app_assert_same_org();
CREATE TRIGGER trg_same_org_person_batches BEFORE INSERT OR UPDATE ON person_batches
  FOR EACH ROW EXECUTE FUNCTION app_assert_same_org();
CREATE TRIGGER trg_same_org_cameras BEFORE INSERT OR UPDATE ON cameras
  FOR EACH ROW EXECUTE FUNCTION app_assert_same_org();
CREATE TRIGGER trg_same_org_metrics BEFORE INSERT OR UPDATE ON performance_metrics
  FOR EACH ROW EXECUTE FUNCTION app_assert_same_org();
CREATE TRIGGER trg_same_org_comments BEFORE INSERT OR UPDATE ON coach_comments
  FOR EACH ROW EXECUTE FUNCTION app_assert_same_org();
CREATE TRIGGER trg_same_org_ai BEFORE INSERT OR UPDATE ON ai_comments
  FOR EACH ROW EXECUTE FUNCTION app_assert_same_org();
CREATE TRIGGER trg_same_org_unknown BEFORE INSERT OR UPDATE ON unknown_groups
  FOR EACH ROW EXECUTE FUNCTION app_assert_same_org();
CREATE TRIGGER trg_same_org_teachers BEFORE INSERT OR UPDATE ON class_subject_teachers
  FOR EACH ROW EXECUTE FUNCTION app_assert_same_org();

CREATE OR REPLACE FUNCTION app_find_user_by_email(p_email text)
RETURNS TABLE (
  id uuid,
  org_id uuid,
  email text,
  password_hash text,
  full_name text,
  role text,
  status text,
  org_status text
)
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT u.id, u.org_id, u.email, u.password_hash, u.full_name, u.role, u.status, o.status
  FROM users u
  LEFT JOIN organizations o ON o.id = u.org_id
  WHERE lower(u.email) = lower(p_email);
$$;

CREATE OR REPLACE FUNCTION app_find_user_by_id(p_id uuid)
RETURNS TABLE (
  id uuid,
  org_id uuid,
  email text,
  password_hash text,
  full_name text,
  role text,
  status text,
  org_status text
)
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT u.id, u.org_id, u.email, u.password_hash, u.full_name, u.role, u.status, o.status
  FROM users u
  LEFT JOIN organizations o ON o.id = u.org_id
  WHERE u.id = p_id;
$$;

CREATE OR REPLACE FUNCTION app_find_api_key(p_hash text)
RETURNS TABLE (
  id uuid,
  org_id uuid,
  name text,
  rate_limit_per_minute integer,
  revoked_at timestamptz
)
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT id, org_id, name, rate_limit_per_minute, revoked_at
  FROM api_keys
  WHERE key_hash = p_hash;
$$;

CREATE OR REPLACE FUNCTION app_apply_tenant_policy(tbl text, col text) RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
  EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', tbl);
  EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', tbl);
  EXECUTE format('DROP POLICY IF EXISTS tenant_all ON %I', tbl);
  EXECUTE format(
    'CREATE POLICY tenant_all ON %I FOR ALL USING (%I = app_current_org()) WITH CHECK (%I = app_current_org())',
    tbl, col, col
  );
END $$;

SELECT app_apply_tenant_policy('organizations', 'id');
SELECT app_apply_tenant_policy('users', 'org_id');
SELECT app_apply_tenant_policy('org_counters', 'org_id');
SELECT app_apply_tenant_policy('api_keys', 'org_id');
SELECT app_apply_tenant_policy('sports', 'org_id');
SELECT app_apply_tenant_policy('academic_sessions', 'org_id');
SELECT app_apply_tenant_policy('school_classes', 'org_id');
SELECT app_apply_tenant_policy('subjects', 'org_id');
SELECT app_apply_tenant_policy('batches', 'org_id');
SELECT app_apply_tenant_policy('persons', 'org_id');
SELECT app_apply_tenant_policy('person_batches', 'org_id');
SELECT app_apply_tenant_policy('class_subject_teachers', 'org_id');
SELECT app_apply_tenant_policy('cameras', 'org_id');
SELECT app_apply_tenant_policy('attendance_policies', 'org_id');
SELECT app_apply_tenant_policy('holidays', 'org_id');
SELECT app_apply_tenant_policy('leave_requests', 'org_id');
SELECT app_apply_tenant_policy('attendance_records', 'org_id');
SELECT app_apply_tenant_policy('face_images', 'org_id');
SELECT app_apply_tenant_policy('face_embeddings', 'org_id');
SELECT app_apply_tenant_policy('unknown_groups', 'org_id');
SELECT app_apply_tenant_policy('unknown_observations', 'org_id');
SELECT app_apply_tenant_policy('audit_logs', 'org_id');
SELECT app_apply_tenant_policy('notifications', 'org_id');
SELECT app_apply_tenant_policy('performance_metrics', 'org_id');
SELECT app_apply_tenant_policy('coach_comments', 'org_id');
SELECT app_apply_tenant_policy('ai_comments', 'org_id');
SELECT app_apply_tenant_policy('import_jobs', 'org_id');

-- Super-admin accounts have a null org_id, so the users policy above hides them
-- from tenant sessions. Platform routes use smartattendance_admin (BYPASSRLS).

REVOKE ALL ON FUNCTION app_find_user_by_email(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION app_find_user_by_id(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION app_find_api_key(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION app_apply_tenant_policy(text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION app_assert_same_org() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app_find_user_by_email(text) TO smartattendance_app, smartattendance_admin;
GRANT EXECUTE ON FUNCTION app_find_user_by_id(uuid) TO smartattendance_app, smartattendance_admin;
GRANT EXECUTE ON FUNCTION app_find_api_key(text) TO smartattendance_app, smartattendance_admin;
GRANT EXECUTE ON FUNCTION app_assert_same_org() TO smartattendance_app, smartattendance_admin;
GRANT EXECUTE ON FUNCTION app_current_org() TO smartattendance_app, smartattendance_admin;

GRANT USAGE ON SCHEMA public TO smartattendance_app, smartattendance_admin;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO smartattendance_app, smartattendance_admin;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO smartattendance_app, smartattendance_admin;

DO $$
BEGIN
  EXECUTE format('GRANT CONNECT ON DATABASE %I TO smartattendance_app', current_database());
  EXECUTE format('GRANT CONNECT ON DATABASE %I TO smartattendance_admin', current_database());
END $$;
