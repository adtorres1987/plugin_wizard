"""
Project Database Module
Plain SQLite database (no SpatiaLite) for project / assessment / analysis metadata.
"""

import os
import sqlite3


class ProjectDatabase:
    """
    Manages the project.sqlite database.
    Contains: project, assessment, assessment_layer, analysis,
              analysis_criterion, provenance, provenance_step, analysis_result
    """

    def __init__(self, db_path):
        self.db_path = db_path
        self.connection = None

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.connection = sqlite3.connect(self.db_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self._create_schema()
        return self.connection

    def disconnect(self):
        if self.connection:
            self.connection.close()
            self.connection = None

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _create_schema(self):
        statements = [
            """CREATE TABLE IF NOT EXISTS project (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL UNIQUE,
                description TEXT,
                created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at  TEXT,
                is_active   INTEGER NOT NULL DEFAULT 1
            )""",
            """CREATE TABLE IF NOT EXISTS assessment (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id          INTEGER NOT NULL,
                name                TEXT NOT NULL,
                description         TEXT,
                target_layer_name   TEXT,
                target_layer_source TEXT,
                model_type          TEXT,
                created_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at          TEXT,
                is_active           INTEGER NOT NULL DEFAULT 1,
                FOREIGN KEY (project_id) REFERENCES project(id)
            )""",
            """CREATE TABLE IF NOT EXISTS assessment_layer (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                assessment_id INTEGER NOT NULL,
                layer_name    TEXT NOT NULL,
                layer_source  TEXT,
                layer_role    TEXT NOT NULL,
                geometry_type TEXT,
                is_active     INTEGER NOT NULL DEFAULT 1,
                added_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (assessment_id) REFERENCES assessment(id)
            )""",
            """CREATE TABLE IF NOT EXISTS analysis (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                assessment_id      INTEGER NOT NULL,
                parent_analysis_id INTEGER,
                version_number     INTEGER NOT NULL,
                name               TEXT NOT NULL,
                description        TEXT,
                analysis_type      TEXT NOT NULL,
                status             TEXT NOT NULL DEFAULT 'draft',
                config_json        TEXT,
                created_at         TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                executed_at        TEXT,
                created_by         TEXT,
                FOREIGN KEY (assessment_id)      REFERENCES assessment(id),
                FOREIGN KEY (parent_analysis_id) REFERENCES analysis(id)
            )""",
            """CREATE TABLE IF NOT EXISTS analysis_criterion (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                analysis_id          INTEGER NOT NULL,
                assessment_layer_id  INTEGER,
                criterion_name       TEXT NOT NULL,
                weight               REAL,
                score_field          TEXT,
                normalization_method TEXT,
                transform_rule       TEXT,
                is_enabled           INTEGER NOT NULL DEFAULT 1,
                FOREIGN KEY (analysis_id)         REFERENCES analysis(id),
                FOREIGN KEY (assessment_layer_id) REFERENCES assessment_layer(id)
            )""",
            """CREATE TABLE IF NOT EXISTS provenance (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                analysis_id           INTEGER NOT NULL,
                engine_name           TEXT,
                engine_version        TEXT,
                plugin_version        TEXT,
                qgis_version          TEXT,
                execution_started_at  TEXT,
                execution_finished_at TEXT,
                execution_status      TEXT,
                summary_json          TEXT,
                warnings_json         TEXT,
                errors_json           TEXT,
                FOREIGN KEY (analysis_id) REFERENCES analysis(id)
            )""",
            """CREATE TABLE IF NOT EXISTS provenance_step (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                provenance_id    INTEGER NOT NULL,
                step_order       INTEGER NOT NULL,
                step_name        TEXT NOT NULL,
                step_type        TEXT,
                input_refs_json  TEXT,
                parameters_json  TEXT,
                output_refs_json TEXT,
                duration_ms      INTEGER,
                status           TEXT NOT NULL,
                message          TEXT,
                FOREIGN KEY (provenance_id) REFERENCES provenance(id)
            )""",
            """CREATE TABLE IF NOT EXISTS analysis_result (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                analysis_id  INTEGER NOT NULL,
                result_type  TEXT NOT NULL,
                name         TEXT NOT NULL,
                description  TEXT,
                storage_type TEXT NOT NULL,
                storage_ref  TEXT NOT NULL,
                is_primary   INTEGER NOT NULL DEFAULT 0,
                created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                metadata_json TEXT,
                FOREIGN KEY (analysis_id) REFERENCES analysis(id)
            )""",
            # Indexes
            "CREATE INDEX IF NOT EXISTS idx_assessment_project         ON assessment(project_id)",
            "CREATE INDEX IF NOT EXISTS idx_assessment_layer_assessment ON assessment_layer(assessment_id)",
            "CREATE INDEX IF NOT EXISTS idx_analysis_assessment         ON analysis(assessment_id)",
            "CREATE INDEX IF NOT EXISTS idx_analysis_parent             ON analysis(parent_analysis_id)",
            "CREATE INDEX IF NOT EXISTS idx_criterion_analysis          ON analysis_criterion(analysis_id)",
            "CREATE INDEX IF NOT EXISTS idx_provenance_analysis         ON provenance(analysis_id)",
            "CREATE INDEX IF NOT EXISTS idx_prov_step_provenance        ON provenance_step(provenance_id)",
            "CREATE INDEX IF NOT EXISTS idx_result_analysis             ON analysis_result(analysis_id)",
            # Unique version per assessment
            """CREATE UNIQUE INDEX IF NOT EXISTS uq_analysis_version
               ON analysis(assessment_id, version_number)""",
        ]
        for stmt in statements:
            self.connection.execute(stmt)
        self.connection.commit()

    # ------------------------------------------------------------------
    # Project
    # ------------------------------------------------------------------

    def create_project(self, name, description=None):
        cur = self.connection.execute(
            "INSERT INTO project (name, description) VALUES (?, ?)",
            (name, description)
        )
        self.connection.commit()
        return cur.lastrowid

    def list_projects(self, active_only=True):
        q = "SELECT * FROM project"
        q += " WHERE is_active = 1" if active_only else ""
        q += " ORDER BY created_at DESC"
        return [dict(r) for r in self.connection.execute(q).fetchall()]

    def get_project(self, project_id):
        row = self.connection.execute(
            "SELECT * FROM project WHERE id = ?", (project_id,)
        ).fetchone()
        return dict(row) if row else None

    def update_project(self, project_id, name=None, description=None):
        parts, vals = ["updated_at = CURRENT_TIMESTAMP"], []
        if name is not None:
            parts.insert(0, "name = ?"); vals.append(name)
        if description is not None:
            parts.insert(0, "description = ?"); vals.append(description)
        vals.append(project_id)
        self.connection.execute(
            f"UPDATE project SET {', '.join(parts)} WHERE id = ?", vals
        )
        self.connection.commit()

    # ------------------------------------------------------------------
    # Assessment
    # ------------------------------------------------------------------

    def create_assessment(self, project_id, name, description=None,
                          target_layer_name=None, target_layer_source=None,
                          model_type='overlay'):
        cur = self.connection.execute("""
            INSERT INTO assessment
                (project_id, name, description, target_layer_name,
                 target_layer_source, model_type)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (project_id, name, description, target_layer_name,
              target_layer_source, model_type))
        self.connection.commit()
        return cur.lastrowid

    def list_assessments(self, project_id, active_only=True):
        q = "SELECT * FROM assessment WHERE project_id = ?"
        params = [project_id]
        if active_only:
            q += " AND is_active = 1"
        q += " ORDER BY created_at DESC"
        return [dict(r) for r in self.connection.execute(q, params).fetchall()]

    def get_assessment(self, assessment_id):
        row = self.connection.execute(
            "SELECT * FROM assessment WHERE id = ?", (assessment_id,)
        ).fetchone()
        return dict(row) if row else None

    def update_assessment(self, assessment_id, **kwargs):
        allowed = {'name', 'description', 'target_layer_name',
                   'target_layer_source', 'model_type'}
        parts = ["updated_at = CURRENT_TIMESTAMP"]
        vals = []
        for k, v in kwargs.items():
            if k in allowed:
                parts.insert(0, f"{k} = ?")
                vals.append(v)
        vals.append(assessment_id)
        self.connection.execute(
            f"UPDATE assessment SET {', '.join(parts)} WHERE id = ?", vals
        )
        self.connection.commit()

    # ------------------------------------------------------------------
    # Assessment Layer
    # ------------------------------------------------------------------

    def add_assessment_layer(self, assessment_id, layer_name, layer_role,
                              layer_source=None, geometry_type=None):
        cur = self.connection.execute("""
            INSERT INTO assessment_layer
                (assessment_id, layer_name, layer_source, layer_role, geometry_type)
            VALUES (?, ?, ?, ?, ?)
        """, (assessment_id, layer_name, layer_source, layer_role, geometry_type))
        self.connection.commit()
        return cur.lastrowid

    def list_assessment_layers(self, assessment_id):
        return [dict(r) for r in self.connection.execute(
            "SELECT * FROM assessment_layer WHERE assessment_id = ? AND is_active = 1",
            (assessment_id,)
        ).fetchall()]

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------

    def create_analysis(self, assessment_id, name, analysis_type,
                        description=None, parent_analysis_id=None,
                        config_json=None, created_by=None):
        row = self.connection.execute(
            "SELECT COALESCE(MAX(version_number), 0) + 1 FROM analysis WHERE assessment_id = ?",
            (assessment_id,)
        ).fetchone()
        version_number = row[0]
        cur = self.connection.execute("""
            INSERT INTO analysis
                (assessment_id, parent_analysis_id, version_number, name,
                 description, analysis_type, config_json, created_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (assessment_id, parent_analysis_id, version_number, name,
              description, analysis_type, config_json, created_by))
        self.connection.commit()
        return cur.lastrowid

    def list_analyses(self, assessment_id):
        return [dict(r) for r in self.connection.execute(
            "SELECT * FROM analysis WHERE assessment_id = ? ORDER BY version_number",
            (assessment_id,)
        ).fetchall()]

    def get_analysis(self, analysis_id):
        row = self.connection.execute(
            "SELECT * FROM analysis WHERE id = ?", (analysis_id,)
        ).fetchone()
        return dict(row) if row else None

    def update_analysis_status(self, analysis_id, status, executed_at=None):
        self.connection.execute(
            "UPDATE analysis SET status = ?, executed_at = COALESCE(?, executed_at) WHERE id = ?",
            (status, executed_at, analysis_id)
        )
        self.connection.commit()

    # ------------------------------------------------------------------
    # Analysis Criterion
    # ------------------------------------------------------------------

    def add_analysis_criterion(self, analysis_id, criterion_name,
                                assessment_layer_id=None, weight=None,
                                score_field=None, normalization_method=None,
                                transform_rule=None):
        cur = self.connection.execute("""
            INSERT INTO analysis_criterion
                (analysis_id, assessment_layer_id, criterion_name, weight,
                 score_field, normalization_method, transform_rule)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (analysis_id, assessment_layer_id, criterion_name, weight,
              score_field, normalization_method, transform_rule))
        self.connection.commit()
        return cur.lastrowid

    def list_analysis_criteria(self, analysis_id):
        return [dict(r) for r in self.connection.execute(
            "SELECT * FROM analysis_criterion WHERE analysis_id = ? AND is_enabled = 1",
            (analysis_id,)
        ).fetchall()]

    # ------------------------------------------------------------------
    # Provenance
    # ------------------------------------------------------------------

    def create_provenance(self, analysis_id, engine_name=None,
                           engine_version=None, plugin_version=None,
                           qgis_version=None):
        cur = self.connection.execute("""
            INSERT INTO provenance
                (analysis_id, engine_name, engine_version, plugin_version,
                 qgis_version, execution_started_at, execution_status)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, 'running')
        """, (analysis_id, engine_name, engine_version,
              plugin_version, qgis_version))
        self.connection.commit()
        return cur.lastrowid

    def add_provenance_step(self, provenance_id, step_order, step_name, status,
                             step_type=None, message=None, duration_ms=None,
                             input_refs_json=None, parameters_json=None,
                             output_refs_json=None):
        cur = self.connection.execute("""
            INSERT INTO provenance_step
                (provenance_id, step_order, step_name, step_type,
                 input_refs_json, parameters_json, output_refs_json,
                 duration_ms, status, message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (provenance_id, step_order, step_name, step_type,
              input_refs_json, parameters_json, output_refs_json,
              duration_ms, status, message))
        self.connection.commit()
        return cur.lastrowid

    def complete_provenance(self, provenance_id, status='completed',
                             summary_json=None, warnings_json=None,
                             errors_json=None):
        self.connection.execute("""
            UPDATE provenance
            SET execution_status      = ?,
                execution_finished_at = CURRENT_TIMESTAMP,
                summary_json          = ?,
                warnings_json         = ?,
                errors_json           = ?
            WHERE id = ?
        """, (status, summary_json, warnings_json, errors_json, provenance_id))
        self.connection.commit()

    # ------------------------------------------------------------------
    # Analysis Result
    # ------------------------------------------------------------------

    def add_analysis_result(self, analysis_id, result_type, name,
                             storage_type, storage_ref,
                             description=None, is_primary=False,
                             metadata_json=None):
        cur = self.connection.execute("""
            INSERT INTO analysis_result
                (analysis_id, result_type, name, description,
                 storage_type, storage_ref, is_primary, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (analysis_id, result_type, name, description,
              storage_type, storage_ref, 1 if is_primary else 0,
              metadata_json))
        self.connection.commit()
        return cur.lastrowid

    def list_analysis_results(self, analysis_id):
        return [dict(r) for r in self.connection.execute(
            "SELECT * FROM analysis_result WHERE analysis_id = ? ORDER BY is_primary DESC",
            (analysis_id,)
        ).fetchall()]
