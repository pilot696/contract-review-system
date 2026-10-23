-- 合同审批审查系统 · PostgreSQL 16 schema
-- 由 ORM 元数据生成，与 scripts/init_db.py 等价；带 IF NOT EXISTS 的临时列迁移见 init_db.py。

CREATE TABLE approval_tasks (
	id BIGSERIAL NOT NULL, 
	approval_code VARCHAR(64) NOT NULL, 
	approval_title VARCHAR(512) NOT NULL, 
	applicant_name VARCHAR(128), 
	applied_at TIMESTAMP WITH TIME ZONE, 
	attachment_count INTEGER DEFAULT 0 NOT NULL, 
	task_status VARCHAR(16) DEFAULT 'pending' NOT NULL, 
	write_status VARCHAR(16) DEFAULT 'not_written' NOT NULL, 
	failed_stage VARCHAR(16), 
	blocked_reason VARCHAR(32), 
	retry_count INTEGER DEFAULT 0 NOT NULL, 
	last_error TEXT, 
	source VARCHAR(16) DEFAULT 'mock' NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_tasks_task_status CHECK (task_status IN ('pending', 'parsing', 'reviewing', 'blocked', 'done')), 
	CONSTRAINT ck_tasks_write_status CHECK (write_status IN ('not_written', 'writing', 'success', 'failed')), 
	CONSTRAINT ck_tasks_failed_stage CHECK (failed_stage IN ('pull', 'download', 'parse', 'review', 'writeback')), 
	CONSTRAINT ck_tasks_blocked_reason CHECK (blocked_reason IN ('ATTACHMENT_MISSING', 'DOWNLOAD_FAILED', 'OCR_FAILED', 'DOCUMENT_EMPTY', 'PARSE_FAILED', 'APPROVAL_API_ERROR', 'RULE_ENGINE_ERROR', 'WRITEBACK_FAILED', 'INTERNAL_ERROR')), 
	UNIQUE (approval_code)
);
CREATE INDEX idx_tasks_created_at ON approval_tasks (created_at);
CREATE INDEX idx_tasks_status ON approval_tasks (task_status);

CREATE TABLE review_rules (
	id BIGSERIAL NOT NULL, 
	rule_code VARCHAR(32) NOT NULL, 
	rule_name VARCHAR(128) NOT NULL, 
	risk_level VARCHAR(8) NOT NULL, 
	rule_status VARCHAR(16) DEFAULT 'active' NOT NULL, 
	match_mode VARCHAR(24) NOT NULL, 
	match_text TEXT NOT NULL, 
	suggestion_text TEXT NOT NULL, 
	priority INTEGER DEFAULT 100 NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_rules_risk_level CHECK (risk_level IN ('low', 'medium', 'high')), 
	CONSTRAINT ck_rules_rule_status CHECK (rule_status IN ('active', 'inactive')), 
	CONSTRAINT ck_rules_match_mode CHECK (match_mode IN ('keyword', 'regex', 'field_absent', 'numeric_threshold')), 
	UNIQUE (rule_code)
);
CREATE INDEX idx_rules_status ON review_rules (rule_status);

CREATE TABLE approval_attachments (
	id BIGSERIAL NOT NULL, 
	task_id BIGINT NOT NULL, 
	external_attachment_id VARCHAR(64) NOT NULL, 
	file_name VARCHAR(255) NOT NULL, 
	file_type VARCHAR(16) NOT NULL, 
	file_path VARCHAR(1024), 
	file_size BIGINT, 
	file_hash VARCHAR(64), 
	download_status VARCHAR(16) DEFAULT 'pending' NOT NULL, 
	download_error TEXT, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_attachments_file_type CHECK (file_type IN ('pdf', 'docx', 'png', 'jpg', 'jpeg')), 
	CONSTRAINT ck_attachments_download_status CHECK (download_status IN ('pending', 'success', 'failed')), 
	CONSTRAINT uq_attachments_task_external UNIQUE (task_id, external_attachment_id), 
	FOREIGN KEY(task_id) REFERENCES approval_tasks (id) ON DELETE CASCADE
);
CREATE INDEX idx_attachments_task ON approval_attachments (task_id);

CREATE TABLE review_results (
	id BIGSERIAL NOT NULL, 
	task_id BIGINT NOT NULL, 
	overall_risk_level VARCHAR(8) NOT NULL, 
	summary_text TEXT NOT NULL, 
	focus_points_json JSONB DEFAULT '[]'::jsonb NOT NULL, 
	comment_text TEXT NOT NULL, 
	hit_count INTEGER DEFAULT 0 NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_results_overall_risk CHECK (overall_risk_level IN ('low', 'medium', 'high')), 
	UNIQUE (task_id), 
	FOREIGN KEY(task_id) REFERENCES approval_tasks (id) ON DELETE CASCADE
);

CREATE TABLE rule_hits (
	id BIGSERIAL NOT NULL, 
	task_id BIGINT NOT NULL, 
	rule_id BIGINT NOT NULL, 
	evidence_text TEXT NOT NULL, 
	evidence_position VARCHAR(256) NOT NULL, 
	evidence_page INTEGER, 
	evidence_confidence NUMERIC(5, 4), 
	matched_field VARCHAR(64), 
	hit_status VARCHAR(16) DEFAULT 'hit' NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_hits_hit_status CHECK (hit_status IN ('hit', 'skipped')), 
	CONSTRAINT ck_hits_evidence_confidence CHECK (evidence_confidence IS NULL OR (evidence_confidence >= 0 AND evidence_confidence <= 1)), 
	FOREIGN KEY(task_id) REFERENCES approval_tasks (id) ON DELETE CASCADE, 
	FOREIGN KEY(rule_id) REFERENCES review_rules (id)
);
CREATE INDEX idx_hits_rule ON rule_hits (rule_id);
CREATE INDEX idx_hits_task ON rule_hits (task_id);

CREATE TABLE task_logs (
	id BIGSERIAL NOT NULL, 
	task_id BIGINT, 
	log_level VARCHAR(8) NOT NULL, 
	log_type VARCHAR(16) NOT NULL, 
	log_content TEXT NOT NULL, 
	context_json JSONB, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_task_logs_log_level CHECK (log_level IN ('debug', 'info', 'warning', 'error')), 
	CONSTRAINT ck_task_logs_log_type CHECK (log_type IN ('pull', 'download', 'parse', 'review', 'writeback', 'state', 'api')), 
	FOREIGN KEY(task_id) REFERENCES approval_tasks (id) ON DELETE CASCADE
);
CREATE INDEX idx_logs_level ON task_logs (log_level);
CREATE INDEX idx_logs_task_time ON task_logs (task_id, created_at);

CREATE TABLE comment_logs (
	id BIGSERIAL NOT NULL, 
	task_id BIGINT NOT NULL, 
	review_id BIGINT NOT NULL, 
	write_status VARCHAR(16) NOT NULL, 
	idempotency_key VARCHAR(128) NOT NULL, 
	write_response_text TEXT, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_comment_logs_write_status CHECK (write_status IN ('writing', 'success', 'failed')), 
	FOREIGN KEY(task_id) REFERENCES approval_tasks (id) ON DELETE CASCADE, 
	FOREIGN KEY(review_id) REFERENCES review_results (id) ON DELETE CASCADE
);
CREATE INDEX idx_comment_logs_task ON comment_logs (task_id, created_at);
CREATE UNIQUE INDEX uq_comment_success ON comment_logs (idempotency_key) WHERE write_status = 'success';

CREATE TABLE contract_parses (
	id BIGSERIAL NOT NULL, 
	task_id BIGINT NOT NULL, 
	attachment_id BIGINT NOT NULL, 
	basic_info_json JSONB DEFAULT '{}'::jsonb NOT NULL, 
	clause_info_json JSONB DEFAULT '{}'::jsonb NOT NULL, 
	full_text TEXT, 
	text_blocks_json JSONB DEFAULT '[]'::jsonb, 
	parse_status VARCHAR(16) DEFAULT 'pending' NOT NULL, 
	parse_error TEXT, 
	ocr_used BOOLEAN DEFAULT false NOT NULL, 
	page_count INTEGER, 
	parser_version VARCHAR(32) DEFAULT 'deterministic-v1' NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_parses_parse_status CHECK (parse_status IN ('pending', 'success', 'failed')), 
	CONSTRAINT uq_parses_task_attachment UNIQUE (task_id, attachment_id), 
	FOREIGN KEY(task_id) REFERENCES approval_tasks (id) ON DELETE CASCADE, 
	FOREIGN KEY(attachment_id) REFERENCES approval_attachments (id) ON DELETE CASCADE
);
CREATE INDEX idx_parses_task ON contract_parses (task_id);
