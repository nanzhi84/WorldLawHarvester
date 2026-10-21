-- WorldLawHarvester initial baseline. No predecessor data or migrations are required.
CREATE SCHEMA wlh;
CREATE TABLE wlh.migration (
 version integer PRIMARY KEY CHECK (version = 1), checksum text NOT NULL,
 applied_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE FUNCTION wlh.reject_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'immutable_wlh_record'; END $$;
CREATE TABLE wlh.source (
 id text PRIMARY KEY, config jsonb NOT NULL CHECK (jsonb_typeof(config)='object'),
 generation bigint NOT NULL DEFAULT 0, run_seq bigint NOT NULL DEFAULT 0,
 write_seq bigint NOT NULL DEFAULT 0, paused boolean NOT NULL DEFAULT false,
 cooldown_until timestamptz, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE wlh.decision (
 id text PRIMARY KEY, source_id text NOT NULL REFERENCES wlh.source(id),
 kind text NOT NULL CHECK (kind='control'), actor text NOT NULL,
 payload jsonb NOT NULL, idempotency_key text UNIQUE,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER decision_immutable BEFORE UPDATE OR DELETE ON wlh.decision
 FOR EACH ROW EXECUTE FUNCTION wlh.reject_mutation();
CREATE TABLE wlh.run (
 id text PRIMARY KEY, source_id text NOT NULL REFERENCES wlh.source(id),
 mode text NOT NULL CHECK (mode IN ('collect','recheck','reparse')),
 generation bigint NOT NULL, run_seq bigint NOT NULL,
 attempt_epoch int NOT NULL DEFAULT 0 CHECK (attempt_epoch>=0),
 max_attempts int NOT NULL DEFAULT 3 CHECK (max_attempts BETWEEN 1 AND 10),
 deadline timestamptz NOT NULL, job_id bigint, slot text UNIQUE,
 status text NOT NULL DEFAULT 'pending'
 CHECK (status IN ('pending','running','finishing','retryable','complete','failed')),
 recheck_plan jsonb, reparse_plan jsonb, config jsonb NOT NULL,
 config_hash text NOT NULL, parser_manifest jsonb NOT NULL,
 external_requests bigint NOT NULL DEFAULT 0 CHECK (external_requests>=0),
 errors jsonb NOT NULL DEFAULT '[]', report jsonb NOT NULL DEFAULT '{}',
 created_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz,
 CHECK (mode <> 'reparse' OR external_requests=0)
);
CREATE INDEX run_source ON wlh.run(source_id,created_at);
CREATE TABLE wlh.frontier (
 id text PRIMARY KEY, source_id text NOT NULL UNIQUE REFERENCES wlh.source(id),
 run_id text REFERENCES wlh.run(id), checkpoint jsonb NOT NULL DEFAULT '{}',
 updated_at timestamptz NOT NULL DEFAULT now(), UNIQUE (id, source_id)
);
CREATE FUNCTION wlh.sha256_text(value text) RETURNS boolean LANGUAGE sql IMMUTABLE AS
 $$ SELECT value ~ '^[0-9a-f]{64}$' $$;

-- One controlled external raw-data acquisition. Terminal facts are never edited in place.
CREATE TABLE wlh.acquisition (
 id text PRIMARY KEY,
 source_id text NOT NULL REFERENCES wlh.source(id),
 run_id text NOT NULL REFERENCES wlh.run(id),
 attempt_epoch int NOT NULL CHECK (attempt_epoch > 0),
 request_key text NOT NULL CHECK (wlh.sha256_text(request_key)),
 acquisition_kind text NOT NULL CHECK (acquisition_kind IN ('http','rendered_dom')),
 role text NOT NULL CHECK (role IN ('content','policy')),
 http_method text,
 request_url text NOT NULL,
 actual_url text,
 request_meta jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(request_meta)='object'),
 parent_acquisition_id text REFERENCES wlh.acquisition(id),
 parent_relation text CHECK (parent_relation IN ('redirect','retry')),
 mime_type text,
 charset text,
 representation text,
 byte_size bigint CHECK (byte_size IS NULL OR byte_size >= 0),
 body_hash text CHECK (body_hash IS NULL OR wlh.sha256_text(body_hash)),
 archive_ref text,
 state text NOT NULL CHECK (state IN ('running','archived','failed','cancelled','abandoned')),
 http_status int,
 outcome text,
 error text,
 started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 acquired_at timestamptz,
 CHECK ((parent_acquisition_id IS NULL) = (parent_relation IS NULL)),
 CHECK (archive_ref IS NULL OR archive_ref = body_hash),
 CHECK ((state = 'archived') = (archive_ref IS NOT NULL)),
 CHECK (state = 'running' OR acquired_at IS NOT NULL)
);
CREATE INDEX acquisition_run ON wlh.acquisition(run_id, attempt_epoch, state);
CREATE INDEX acquisition_request ON wlh.acquisition(source_id, request_key);
CREATE FUNCTION wlh.acquisition_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP <> 'UPDATE' OR OLD.state <> 'running' OR NEW.state = 'running' OR
   (to_jsonb(OLD) - ARRAY['state','http_status','outcome','error','acquired_at','actual_url',
     'mime_type','charset','representation','byte_size','body_hash','archive_ref']) <>
   (to_jsonb(NEW) - ARRAY['state','http_status','outcome','error','acquired_at','actual_url',
     'mime_type','charset','representation','byte_size','body_hash','archive_ref']) THEN
  RAISE EXCEPTION 'immutable_acquisition';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER acquisition_terminal BEFORE UPDATE OR DELETE ON wlh.acquisition
 FOR EACH ROW EXECUTE FUNCTION wlh.acquisition_guard();

-- Persistent request tasks and document targets. Three completion points stay distinct:
-- archived input (request task archived), registered target (target row exists) and
-- delivered target (observation committed; target done/failed).
CREATE TABLE wlh.task (
 id text PRIMARY KEY,
 run_id text NOT NULL REFERENCES wlh.run(id),
 seq bigserial NOT NULL,
 kind text NOT NULL CHECK (kind IN ('request','target')),
 key text NOT NULL CHECK (wlh.sha256_text(key)),
 payload jsonb NOT NULL CHECK (jsonb_typeof(payload)='object'),
 depth int NOT NULL DEFAULT 0 CHECK (depth >= 0),
 state text NOT NULL DEFAULT 'pending'
  CHECK (state IN ('pending','archived','done','failed','skipped')),
 acquisition_id text REFERENCES wlh.acquisition(id),
 observation_id text,
 error text,
 attempt_epoch int,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE (run_id, kind, key)
);
CREATE INDEX task_queue ON wlh.task(run_id, kind, state, seq);
CREATE TABLE wlh.task_input (
 target_id text NOT NULL REFERENCES wlh.task(id),
 request_id text NOT NULL REFERENCES wlh.task(id),
 position int NOT NULL CHECK (position >= 0),
 PRIMARY KEY (target_id, request_id)
);
CREATE INDEX task_input_request ON wlh.task_input(request_id);

CREATE TABLE wlh.resource (
 id text PRIMARY KEY,
 frontier_id text NOT NULL REFERENCES wlh.frontier(id),
 identity_namespace text NOT NULL CHECK (identity_namespace <> ''),
 external_key text NOT NULL CHECK (external_key <> ''),
 public_url text,
 recheck_locator jsonb NOT NULL CHECK (jsonb_typeof(recheck_locator)='object'),
 current_version_id text,
 last_checked_at timestamptz,
 last_outcome text,
 next_check timestamptz NOT NULL DEFAULT now(),
 UNIQUE (frontier_id, identity_namespace, external_key),
 UNIQUE (id, frontier_id)
);
CREATE INDEX resource_due ON wlh.resource(frontier_id, next_check);

CREATE TABLE wlh.observation (
 id text PRIMARY KEY,
 resource_id text NOT NULL REFERENCES wlh.resource(id),
 run_id text NOT NULL REFERENCES wlh.run(id),
 attempt_epoch int NOT NULL,
 delivery_key text NOT NULL UNIQUE,
 outcome text NOT NULL CHECK (outcome IN ('created','changed','unchanged','failed')),
 change_reason text,
 error text,
 inputs_ref text CHECK (inputs_ref IS NULL OR wlh.sha256_text(inputs_ref)),
 result_ref text CHECK (result_ref IS NULL OR wlh.sha256_text(result_ref)),
 input_fingerprint text CHECK (input_fingerprint IS NULL OR wlh.sha256_text(input_fingerprint)),
 parser_fingerprint text CHECK (parser_fingerprint IS NULL OR wlh.sha256_text(parser_fingerprint)),
 version_fingerprint text CHECK (version_fingerprint IS NULL OR wlh.sha256_text(version_fingerprint)),
 content_hash text CHECK (content_hash IS NULL OR wlh.sha256_text(content_hash)),
 version_id text,
 observed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK (outcome = 'failed' OR (inputs_ref IS NOT NULL AND result_ref IS NOT NULL
   AND version_id IS NOT NULL AND content_hash IS NOT NULL AND version_fingerprint IS NOT NULL)),
 CHECK (outcome <> 'failed' OR (version_id IS NULL AND error IS NOT NULL))
);
CREATE INDEX observation_run ON wlh.observation(run_id);
CREATE TRIGGER observation_immutable BEFORE UPDATE OR DELETE ON wlh.observation
 FOR EACH ROW EXECUTE FUNCTION wlh.reject_mutation();
-- Later same-semantics acquisitions are appended as evidence; never merged into inputs_ref.
CREATE TABLE wlh.observation_evidence (
 observation_id text NOT NULL REFERENCES wlh.observation(id),
 acquisition_id text NOT NULL REFERENCES wlh.acquisition(id),
 relation text NOT NULL CHECK (relation IN ('input','replay')),
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY (observation_id, acquisition_id, relation)
);
CREATE TRIGGER observation_evidence_immutable BEFORE UPDATE OR DELETE ON wlh.observation_evidence
 FOR EACH ROW EXECUTE FUNCTION wlh.reject_mutation();
CREATE TABLE wlh.delivery_conflict (
 id text PRIMARY KEY,
 run_id text NOT NULL REFERENCES wlh.run(id),
 resource_id text NOT NULL REFERENCES wlh.resource(id),
 attempt_epoch int NOT NULL,
 reason text NOT NULL,
 existing_observation_id text REFERENCES wlh.observation(id),
 attempted jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TRIGGER delivery_conflict_immutable BEFORE UPDATE OR DELETE ON wlh.delivery_conflict
 FOR EACH ROW EXECUTE FUNCTION wlh.reject_mutation();

CREATE SEQUENCE wlh.version_cursor;
CREATE TABLE wlh.version (
 id text PRIMARY KEY,
 resource_id text NOT NULL REFERENCES wlh.resource(id),
 sequence int NOT NULL CHECK (sequence > 0),
 observation_id text NOT NULL UNIQUE REFERENCES wlh.observation(id) DEFERRABLE INITIALLY DEFERRED,
 result_ref text NOT NULL CHECK (wlh.sha256_text(result_ref)),
 inputs_ref text NOT NULL CHECK (wlh.sha256_text(inputs_ref)),
 input_fingerprint text NOT NULL CHECK (wlh.sha256_text(input_fingerprint)),
 parser_fingerprint text NOT NULL CHECK (wlh.sha256_text(parser_fingerprint)),
 version_fingerprint text NOT NULL CHECK (wlh.sha256_text(version_fingerprint)),
 content_hash text NOT NULL CHECK (wlh.sha256_text(content_hash)),
 parser_config jsonb NOT NULL,
 change_reason text NOT NULL,
 cursor bigint NOT NULL DEFAULT nextval('wlh.version_cursor'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE (id, resource_id),
 UNIQUE (resource_id, sequence),
 UNIQUE (observation_id, resource_id, result_ref)
);
CREATE TRIGGER version_immutable BEFORE UPDATE OR DELETE ON wlh.version
 FOR EACH ROW EXECUTE FUNCTION wlh.reject_mutation();
ALTER TABLE wlh.observation ADD CONSTRAINT observation_version_fk FOREIGN KEY (version_id, resource_id)
 REFERENCES wlh.version(id, resource_id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE wlh.resource ADD CONSTRAINT resource_current_version_fk
 FOREIGN KEY (current_version_id, id) REFERENCES wlh.version(id, resource_id)
 DEFERRABLE INITIALLY DEFERRED;

-- One row per delivered Markdown file and optional original file.
CREATE TABLE wlh.document_row (
 version_id text NOT NULL REFERENCES wlh.version(id),
 row_index int NOT NULL CHECK (row_index > 0),
 title text NOT NULL CHECK (title <> ''),
 markdown_file text NOT NULL CHECK (markdown_file ~ '\.md$'),
 doc_type text NOT NULL CHECK (doc_type IN
  ('法律法规','政策文件','案例','行业标准','通知公告','财务报告','会计准则','税务资料','其他')),
 acquired_at timestamptz NOT NULL,
 original_file text,
 volume_order int CHECK (volume_order IS NULL OR volume_order > 0),
 doc_number text,
 issuer text,
 published_at date,
 effective_at date,
 expired_at date,
 status text,
 source_name text,
 detail_url text,
 source_record_hash text NOT NULL CHECK (wlh.sha256_text(source_record_hash)),
 markdown_hash text NOT NULL CHECK (wlh.sha256_text(markdown_hash)),
 original_hash text CHECK (original_hash IS NULL OR wlh.sha256_text(original_hash)),
 original_media_type text,
 role text NOT NULL CHECK (role IN ('primary','volume','independent_attachment')),
 locator jsonb NOT NULL,
 extensions jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(extensions)='object'),
 PRIMARY KEY (version_id, row_index),
 CHECK ((original_hash IS NULL) = (original_file IS NULL)),
 CHECK ((role = 'volume') = (volume_order IS NOT NULL))
);
CREATE INDEX document_row_original ON wlh.document_row(original_hash) WHERE original_hash IS NOT NULL;
CREATE INDEX document_row_markdown ON wlh.document_row(markdown_hash);
CREATE TRIGGER document_row_immutable BEFORE UPDATE OR DELETE ON wlh.document_row
 FOR EACH ROW EXECUTE FUNCTION wlh.reject_mutation();

CREATE TABLE wlh.snapshot (
 id text PRIMARY KEY,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 sealed boolean NOT NULL DEFAULT false,
 source_count bigint NOT NULL DEFAULT 0 CHECK (source_count >= 0),
 resource_count bigint NOT NULL DEFAULT 0 CHECK (resource_count >= 0),
 version_count bigint NOT NULL DEFAULT 0 CHECK (version_count >= 0),
 row_count bigint NOT NULL DEFAULT 0 CHECK (row_count >= 0),
 file_count bigint NOT NULL DEFAULT 0 CHECK (file_count >= 0)
);
CREATE TABLE wlh.snapshot_source (
 snapshot_id text NOT NULL REFERENCES wlh.snapshot(id),
 source_id text NOT NULL REFERENCES wlh.source(id),
 frontier_id text NOT NULL REFERENCES wlh.frontier(id),
 coverage jsonb NOT NULL CHECK (jsonb_typeof(coverage)='object'),
 PRIMARY KEY (snapshot_id, source_id),
 UNIQUE (snapshot_id, source_id, frontier_id)
);
CREATE TABLE wlh.snapshot_item (
 snapshot_id text NOT NULL,
 source_id text NOT NULL,
 frontier_id text NOT NULL,
 resource_id text NOT NULL,
 version_id text NOT NULL,
 PRIMARY KEY (snapshot_id, resource_id),
 FOREIGN KEY (snapshot_id, source_id, frontier_id)
  REFERENCES wlh.snapshot_source(snapshot_id, source_id, frontier_id),
 FOREIGN KEY (resource_id, frontier_id) REFERENCES wlh.resource(id, frontier_id),
 FOREIGN KEY (version_id, resource_id) REFERENCES wlh.version(id, resource_id)
);
CREATE INDEX snapshot_item_source ON wlh.snapshot_item(snapshot_id, source_id COLLATE "C", resource_id COLLATE "C");
-- Closure of downloadable bytes (originals and Markdown) per authorized Source.
CREATE TABLE wlh.snapshot_file (
 snapshot_id text NOT NULL,
 source_id text NOT NULL,
 sha256 text NOT NULL CHECK (wlh.sha256_text(sha256)),
 kind text NOT NULL CHECK (kind IN ('original','markdown')),
 media_type text NOT NULL,
 file_name text NOT NULL,
 byte_size bigint NOT NULL CHECK (byte_size >= 0),
 PRIMARY KEY (snapshot_id, source_id, sha256, kind),
 FOREIGN KEY (snapshot_id, source_id) REFERENCES wlh.snapshot_source(snapshot_id, source_id)
);
CREATE INDEX snapshot_file_hash ON wlh.snapshot_file(snapshot_id, sha256);

CREATE FUNCTION wlh.snapshot_mapping_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE frozen boolean;
BEGIN
 IF TG_OP <> 'INSERT' THEN RAISE EXCEPTION 'immutable_snapshot_mapping'; END IF;
 SELECT sealed INTO frozen FROM wlh.snapshot WHERE id=NEW.snapshot_id FOR UPDATE;
 IF frozen IS DISTINCT FROM false THEN RAISE EXCEPTION 'immutable_snapshot_mapping'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER snapshot_source_immutable BEFORE INSERT OR UPDATE OR DELETE ON wlh.snapshot_source
 FOR EACH ROW EXECUTE FUNCTION wlh.snapshot_mapping_guard();
CREATE TRIGGER snapshot_item_immutable BEFORE INSERT OR UPDATE OR DELETE ON wlh.snapshot_item
 FOR EACH ROW EXECUTE FUNCTION wlh.snapshot_mapping_guard();
CREATE TRIGGER snapshot_file_immutable BEFORE INSERT OR UPDATE OR DELETE ON wlh.snapshot_file
 FOR EACH ROW EXECUTE FUNCTION wlh.snapshot_mapping_guard();

CREATE FUNCTION wlh.snapshot_seal() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE counters text[] := ARRAY['sealed','source_count','resource_count','version_count',
  'row_count','file_count'];
BEGIN
 IF TG_OP = 'INSERT' THEN
  IF NEW.sealed OR NEW.source_count<>0 OR NEW.resource_count<>0 OR NEW.version_count<>0
    OR NEW.row_count<>0 OR NEW.file_count<>0 THEN
   RAISE EXCEPTION 'invalid_snapshot';
  END IF;
  RETURN NEW;
 END IF;
 IF TG_OP <> 'UPDATE' OR OLD.sealed OR NOT NEW.sealed OR
   (to_jsonb(OLD)-counters) <> (to_jsonb(NEW)-counters) THEN
  RAISE EXCEPTION 'immutable_snapshot';
 END IF;
 IF NEW.source_count <> (SELECT count(*) FROM wlh.snapshot_source WHERE snapshot_id=OLD.id)
 OR NEW.resource_count <> (SELECT count(*) FROM wlh.snapshot_item WHERE snapshot_id=OLD.id)
 OR NEW.version_count <> (SELECT count(DISTINCT version_id) FROM wlh.snapshot_item
                          WHERE snapshot_id=OLD.id)
 OR NEW.row_count <> (SELECT count(*) FROM wlh.snapshot_item i
                      JOIN wlh.document_row d ON d.version_id=i.version_id WHERE i.snapshot_id=OLD.id)
 OR NEW.file_count <> (SELECT count(*) FROM wlh.snapshot_file WHERE snapshot_id=OLD.id)
 THEN RAISE EXCEPTION 'invalid_snapshot_counts'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER snapshot_immutable BEFORE INSERT OR UPDATE OR DELETE ON wlh.snapshot
 FOR EACH ROW EXECUTE FUNCTION wlh.snapshot_seal();
CREATE FUNCTION wlh.snapshot_require_sealed() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NOT (SELECT sealed FROM wlh.snapshot WHERE id=NEW.id) THEN
  RAISE EXCEPTION 'snapshot_not_sealed';
 END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER snapshot_sealed_commit AFTER INSERT ON wlh.snapshot
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION wlh.snapshot_require_sealed();

CREATE TABLE wlh.publication (
 singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
 published_snapshot_id text REFERENCES wlh.snapshot(id)
);
INSERT INTO wlh.publication(singleton) VALUES (true);
CREATE TABLE wlh.snapshot_publication (
 sequence bigserial PRIMARY KEY,
 before_snapshot_id text REFERENCES wlh.snapshot(id),
 after_snapshot_id text NOT NULL REFERENCES wlh.snapshot(id),
 published_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK (before_snapshot_id IS DISTINCT FROM after_snapshot_id)
);
CREATE TRIGGER snapshot_publication_immutable BEFORE UPDATE OR DELETE ON wlh.snapshot_publication
 FOR EACH ROW EXECUTE FUNCTION wlh.reject_mutation();
