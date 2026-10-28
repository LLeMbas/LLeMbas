-- The 1.7.3 shape of the tables the import from lembas.db.old reads, dumped from
-- a fresh 1.7.3 database (sqlite3 .schema). Full-text tables and triggers left out.
CREATE TABLE users (
	email VARCHAR(320) NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	password_hash TEXT NOT NULL, 
	role VARCHAR(16) NOT NULL, 
	active BOOLEAN NOT NULL, 
	last_login_at DATETIME, 
	settings_json JSON NOT NULL, 
	plan_id VARCHAR(32), 
	plan_renews_at DATETIME, 
	id VARCHAR(32) NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	CONSTRAINT pk_users PRIMARY KEY (id)
);
CREATE UNIQUE INDEX ix_users_email ON users (email);
CREATE TABLE data_groups (
	id VARCHAR(32) NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	description TEXT NOT NULL, 
	position INTEGER NOT NULL, 
	owner_id VARCHAR(32), 
	embedding_model_id VARCHAR(300) NOT NULL, 
	embedding_connection_id VARCHAR(32) NOT NULL, 
	review_model_id VARCHAR(300) NOT NULL, 
	review_connection_id VARCHAR(32) NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	CONSTRAINT pk_data_groups PRIMARY KEY (id), 
	CONSTRAINT fk_data_groups_owner_id_users FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE CASCADE
);
CREATE INDEX ix_data_groups_owner_id ON data_groups (owner_id);
CREATE TABLE notes (
	owner_id VARCHAR(32) NOT NULL, 
	title VARCHAR(300) NOT NULL, 
	body TEXT NOT NULL, 
	author VARCHAR(16) NOT NULL, 
	id VARCHAR(32) NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	data_group_id VARCHAR(32), 
	CONSTRAINT pk_notes PRIMARY KEY (id), 
	CONSTRAINT fk_notes_owner_id_users FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE CASCADE
);
CREATE INDEX ix_notes_owner_id ON notes (owner_id);
CREATE TABLE reports (
	owner_id VARCHAR(32) NOT NULL, 
	title VARCHAR(300) NOT NULL, 
	summary VARCHAR(500) NOT NULL, 
	body TEXT NOT NULL, 
	source VARCHAR(16) NOT NULL, 
	source_id VARCHAR(32) NOT NULL, 
	schedule_id VARCHAR(32) NOT NULL, 
	model_id VARCHAR(300) NOT NULL, 
	unread BOOLEAN NOT NULL, 
	unread_notified BOOLEAN NOT NULL, 
	error TEXT NOT NULL, 
	id VARCHAR(32) NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	data_group_id VARCHAR(32), 
	CONSTRAINT pk_reports PRIMARY KEY (id), 
	CONSTRAINT fk_reports_owner_id_users FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE CASCADE
);
CREATE INDEX ix_reports_owner_id ON reports (owner_id);
CREATE TABLE skills (
	owner_id VARCHAR(32) NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	description TEXT NOT NULL, 
	body TEXT NOT NULL, 
	enabled BOOLEAN NOT NULL, 
	author VARCHAR(16) NOT NULL, 
	id VARCHAR(32) NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	data_group_id VARCHAR(32), 
	CONSTRAINT pk_skills PRIMARY KEY (id), 
	CONSTRAINT uq_skills_owner_id UNIQUE (owner_id, name), 
	CONSTRAINT fk_skills_owner_id_users FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE CASCADE
);
CREATE INDEX ix_skills_owner_id ON skills (owner_id);
CREATE TABLE skill_revisions (
	skill_id VARCHAR(32) NOT NULL, 
	description TEXT NOT NULL, 
	body TEXT NOT NULL, 
	author VARCHAR(16) NOT NULL, 
	note VARCHAR(200) NOT NULL, 
	id VARCHAR(32) NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	CONSTRAINT pk_skill_revisions PRIMARY KEY (id), 
	CONSTRAINT fk_skill_revisions_skill_id_skills FOREIGN KEY(skill_id) REFERENCES skills (id) ON DELETE CASCADE
);
CREATE INDEX ix_skill_revisions_skill_id ON skill_revisions (skill_id);
CREATE TABLE knowledge_bases (
	owner_id VARCHAR(32) NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	description TEXT NOT NULL, 
	id VARCHAR(32) NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	data_group_id VARCHAR(32), 
	CONSTRAINT pk_knowledge_bases PRIMARY KEY (id), 
	CONSTRAINT uq_knowledge_bases_owner_id UNIQUE (owner_id, name), 
	CONSTRAINT fk_knowledge_bases_owner_id_users FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE CASCADE
);
CREATE INDEX ix_knowledge_bases_owner_id ON knowledge_bases (owner_id);
CREATE TABLE documents (
	owner_id VARCHAR(32) NOT NULL, 
	base_id VARCHAR(32), 
	title VARCHAR(300) NOT NULL, 
	description TEXT NOT NULL, 
	source VARCHAR(16) NOT NULL, 
	source_url TEXT NOT NULL, 
	filename VARCHAR(300) NOT NULL, 
	stored_name VARCHAR(120) NOT NULL, 
	media_type VARCHAR(100) NOT NULL, 
	size_bytes INTEGER NOT NULL, 
	kind VARCHAR(16) NOT NULL, 
	width INTEGER NOT NULL, 
	height INTEGER NOT NULL, 
	extracted_text TEXT NOT NULL, 
	pages INTEGER NOT NULL, 
	truncated BOOLEAN NOT NULL, 
	extraction_error TEXT NOT NULL, 
	id VARCHAR(32) NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	CONSTRAINT pk_documents PRIMARY KEY (id), 
	CONSTRAINT fk_documents_owner_id_users FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE CASCADE, 
	CONSTRAINT fk_documents_base_id_knowledge_bases FOREIGN KEY(base_id) REFERENCES knowledge_bases (id) ON DELETE CASCADE
);
CREATE INDEX ix_documents_owner_id ON documents (owner_id);
CREATE INDEX ix_documents_base_id ON documents (base_id);
CREATE TABLE memories (
	owner_id VARCHAR(32) NOT NULL, 
	content TEXT NOT NULL, 
	author VARCHAR(16) NOT NULL, 
	id VARCHAR(32) NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	data_group_id VARCHAR(32), 
	CONSTRAINT pk_memories PRIMARY KEY (id), 
	CONSTRAINT fk_memories_owner_id_users FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE CASCADE
);
CREATE INDEX ix_memories_owner_id ON memories (owner_id);
PRAGMA user_version = 0;
