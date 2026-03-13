"""
Spatial Analysis Module
Handles spatial operations between target and assessment layers
Creates resulting layers in QGIS memory or SpatiaLite
"""

from qgis.core import QgsVectorLayer, QgsProject, QgsDataSourceUri


class SpatialAnalyzer:
    """
    Performs spatial analysis operations on SpatiaLite tables
    and creates resulting layers in QGIS
    """

    def __init__(self, db_manager):
        self.db_manager = db_manager

    def cumulative_overlay(self, target_table, assessment_tables, output_table, layer_name=None):
        """
        Perform a cumulative overlay of target_table with each table in assessment_tables.

        Each assessment layer progressively splits the geometry fragments produced by the
        previous step, preserving all boundaries. The result is a single output layer
        whose features carry the original target id (input_id) plus the id of the
        assessment feature that covers each fragment (a0_id, a1_id, … or NULL when
        no assessment feature overlaps that fragment).

        Args:
            target_table: Name of the starting (target) SpatiaLite table
            assessment_tables: Ordered list of assessment table names
            output_table: Name of the final output table to create
            layer_name: Display name for the resulting QGIS layer

        Returns:
            dict: {'total_count', 'output_table', 'layer', 'layer_name', 'success'}
        """
        if not self.db_manager.connection:
            raise Exception("Database not connected. Call db_manager.connect() first.")

        if not self.db_manager.table_exists(target_table):
            raise Exception(f"Target table '{target_table}' does not exist.")

        for t in assessment_tables:
            if not self.db_manager.table_exists(t):
                raise Exception(f"Assessment table '{t}' does not exist.")

        self.db_manager.drop_table(output_table)
        cursor = self.db_manager.connection.cursor()

        try:
            # Step 0: seed the working temp table from the target selection
            cursor.execute(f"""
                CREATE TEMP TABLE _overlay_current AS
                SELECT id AS input_id, geom
                FROM {target_table}
                WHERE ST_IsValid(geom)
            """)

            id_columns = []  # accumulates 'a0_id', 'a1_id', ...

            for i, assessment_table in enumerate(assessment_tables):
                col_name = f"a{i}_id"
                id_columns.append(col_name)
                prior_cols = id_columns[:-1]

                prior_select = (", ".join(f"c.{c}" for c in prior_cols) + ", ") if prior_cols else ""
                # SQLite has no NULL::integer cast — use CAST(NULL AS INTEGER) or just NULL
                prior_null = (", ".join(f"CAST(c.{c} AS INTEGER)" for c in prior_cols) + ", ") if prior_cols else ""

                cursor.execute(f"""
                    CREATE TEMP TABLE _overlay_next AS
                    WITH intersected AS (
                        SELECT
                            c.input_id,
                            {prior_select}a.id AS {col_name},
                            ST_Intersection(c.geom, a.geom) AS geom
                        FROM _overlay_current c
                        JOIN {assessment_table} a
                          ON MbrIntersects(c.geom, a.geom)
                         AND ST_Intersects(c.geom, a.geom)
                        WHERE ST_IsValid(c.geom)
                          AND ST_IsValid(a.geom)
                    ),
                    filtered AS (
                        SELECT * FROM intersected
                        WHERE GeometryType(geom) IN ('POLYGON', 'MULTIPOLYGON')
                          AND ST_IsValid(geom)
                          AND ST_Area(geom) > 0
                    ),
                    remainder AS (
                        SELECT
                            c.input_id,
                            {prior_null}NULL AS {col_name},
                            c.geom
                        FROM _overlay_current c
                        WHERE NOT EXISTS (
                            SELECT 1 FROM {assessment_table} a
                            WHERE MbrIntersects(c.geom, a.geom)
                              AND ST_Intersects(c.geom, a.geom)
                        )
                        AND ST_IsValid(c.geom)
                    )
                    SELECT * FROM filtered
                    UNION ALL
                    SELECT * FROM remainder
                """)

                cursor.execute("DROP TABLE _overlay_current")
                cursor.execute("ALTER TABLE _overlay_next RENAME TO _overlay_current")

            # --- Build the final registered SpatiaLite output table ---
            # Get SRID from target table
            cursor.execute(
                "SELECT srid FROM geometry_columns WHERE f_table_name = ?",
                (target_table,)
            )
            row = cursor.fetchone()
            srid = row[0] if row else 4326

            # Column definitions for assessment id columns
            id_col_defs = (", ".join(f"{c} INTEGER" for c in id_columns) + ", ") if id_columns else ""
            id_col_insert = (", ".join(id_columns) + ", ") if id_columns else ""

            # Create table with explicit columns (geometry registered via AddGeometryColumn)
            cursor.execute(f"""
                CREATE TABLE {output_table} (
                    gid     INTEGER PRIMARY KEY AUTOINCREMENT,
                    input_id INTEGER,
                    {id_col_defs}shape_area   REAL,
                    shape_length REAL
                )
            """)
            cursor.execute(
                f"SELECT AddGeometryColumn('{output_table}', 'geom', {srid}, 'GEOMETRY', 2)"
            )
            self.db_manager.connection.commit()

            # Insert from the last overlay temp table
            cursor.execute(f"""
                INSERT INTO {output_table} (input_id, {id_col_insert}geom, shape_area, shape_length)
                SELECT
                    input_id,
                    {id_col_insert}geom,
                    ST_Area(geom),
                    ST_Perimeter(geom)
                FROM _overlay_current
                WHERE GeometryType(geom) IN ('POLYGON', 'MULTIPOLYGON')
                  AND ST_IsValid(geom)
            """)

            cursor.execute("DROP TABLE IF EXISTS _overlay_current")
            self.db_manager.connection.commit()

            cursor.execute(f"SELECT COUNT(*) FROM {output_table}")
            total_count = cursor.fetchone()[0]
            self.db_manager.create_spatial_index(output_table)
            cursor.close()

            layer = self._create_qgis_layer(output_table, layer_name)

            return {
                'total_count': total_count,
                'output_table': output_table,
                'layer': layer,
                'layer_name': layer.name() if layer else None,
                'success': layer is not None
            }

        except Exception as e:
            self.db_manager.connection.rollback()
            cursor.close()
            raise Exception(f"Cumulative overlay failed: {str(e)}")

    def _create_qgis_layer(self, table_name, layer_name=None):
        """Create a QGIS vector layer from a SpatiaLite table."""
        if not layer_name:
            layer_name = table_name

        uri = QgsDataSourceUri()
        uri.setDatabase(self.db_manager.db_path)
        uri.setDataSource("", table_name, "geom", "", "gid")

        layer = QgsVectorLayer(uri.uri(), layer_name, "spatialite")

        if not layer.isValid():
            raise Exception(f"Failed to create QGIS layer from table '{table_name}'")

        QgsProject.instance().addMapLayer(layer)
        return layer
