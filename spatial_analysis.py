"""
Spatial Analysis Module
Handles spatial operations between target and assessment layers
Creates resulting layers in QGIS memory or PostgreSQL
"""

from enum import Enum
from qgis.core import QgsVectorLayer, QgsProject, QgsDataSourceUri, QgsWkbTypes


class OperationType(Enum):
    """Types of spatial operations"""
    INTERSECT = "intersect"          # Only intersected portions (ST_Intersection)
    UNION = "union"                  # ST_Union - dissolve all geometries into one
    BOTH = "both"                    # Both operations combined


class SpatialAnalyzer:
    """
    Performs spatial analysis operations on PostgreSQL/PostGIS tables
    and creates resulting layers in QGIS
    """

    def __init__(self, db_manager):
        """
        Initialize spatial analyzer with database manager

        Args:
            db_manager: DatabaseManager instance with active connection
        """
        self.db_manager = db_manager

    def analyze_and_create_layer(self, target_table, assessment_table, output_table,
                                 layer_name=None, operation_type=OperationType.BOTH):
        """
        Perform spatial analysis between target and assessment layers and create QGIS layer

        Args:
            target_table: Name of target layer table (e.g., 'landuse_a_free')
            assessment_table: Name of assessment layer table (e.g., 'natural_a_free')
            output_table: Name of output table to create in PostgreSQL
            layer_name: Name for the new QGIS layer (defaults to output_table name)
            operation_type: Type of operation (INTERSECT, UNION, or BOTH)

        Returns:
            dict: Results including statistics and the created QgsVectorLayer

        Raises:
            Exception: If analysis fails
        """
        if not self.db_manager.connection:
            raise Exception("Database not connected. Call db_manager.connect() first.")

        # Validate that input tables exist
        if not self.db_manager.table_exists(target_table):
            raise Exception(f"Target table '{target_table}' does not exist")

        if not self.db_manager.table_exists(assessment_table):
            raise Exception(f"Assessment table '{assessment_table}' does not exist")

        # Validate geometry compatibility
        validation = self.validate_geometry_compatibility(target_table, assessment_table)
        if not validation['compatible']:
            raise Exception(f"Geometry incompatibility: {validation['message']}")

        # Drop output table if it exists
        self.db_manager.drop_table(output_table)

        # Build and execute the spatial analysis query
        if operation_type == OperationType.INTERSECT:
            query = self._build_intersect_query(target_table, assessment_table, output_table)
        elif operation_type == OperationType.UNION:
            query = self._build_union_query(target_table, assessment_table, output_table)
        else:  # BOTH
            query = self._build_both_query(target_table, assessment_table, output_table)

        cursor = self.db_manager.connection.cursor()

        try:
            # Execute the query
            cursor.execute(query)
            self.db_manager.connection.commit()

            # Get statistics - total count
            cursor.execute(f"SELECT COUNT(*) FROM {output_table}")
            total_count = cursor.fetchone()[0]

            # Create spatial index on output table
            self.db_manager.create_spatial_index(output_table)

            cursor.close()

            # Create QGIS layer from the PostgreSQL table
            layer = self._create_qgis_layer(output_table, layer_name)

            return {
                'total_count': total_count,
                'output_table': output_table,
                'layer': layer,
                'layer_name': layer.name() if layer else None,
                'success': layer is not None
            }

        except Exception as e:
            cursor.close()
            raise Exception(f"Spatial analysis failed: {str(e)}")

    def _create_qgis_layer(self, table_name, layer_name=None):
        """
        Create a QGIS vector layer from a PostgreSQL table

        Args:
            table_name: Name of the PostgreSQL table
            layer_name: Display name for the layer (defaults to table_name)

        Returns:
            QgsVectorLayer: The created layer, or None if failed
        """
        if not layer_name:
            layer_name = table_name

        # Build PostgreSQL connection URI
        uri = QgsDataSourceUri()
        uri.setConnection(
            self.db_manager.host,
            self.db_manager.port,
            self.db_manager.database,
            self.db_manager.user,
            self.db_manager.password
        )

        # Set the table and geometry column
        uri.setDataSource("public", table_name, "geom", "", "gid")

        # Create the vector layer
        layer = QgsVectorLayer(uri.uri(), layer_name, "postgres")

        if not layer.isValid():
            raise Exception(f"Failed to create QGIS layer from table '{table_name}'")

        # Add layer to QGIS project
        QgsProject.instance().addMapLayer(layer)

        return layer

    def _build_intersect_query(self, target_table, assessment_table, output_table):
        """
        Build query for intersection operation only

        Args:
            target_table: Name of target layer table
            assessment_table: Name of assessment layer table
            output_table: Name of output table

        Returns:
            str: SQL query
        """
        return f"""
        CREATE TABLE {output_table} AS
        WITH intersected AS (
            SELECT
                i.id AS input_id,
                n.id AS identity_id,
                ST_Intersection(i.geom, n.geom) AS geom,
                'intersect' AS split_type
            FROM {target_table} i
            JOIN {assessment_table} n
              ON i.geom && n.geom
             AND ST_Intersects(i.geom, n.geom)
            WHERE ST_IsValid(i.geom)
              AND ST_IsValid(n.geom)
        )
        SELECT
            ROW_NUMBER() OVER () AS gid,
            input_id,
            identity_id,
            geom,
            split_type,
            ST_Area(geom) AS shape_area,
            ST_Perimeter(geom) AS shape_length
        FROM intersected
        WHERE GeometryType(geom) IN ('POLYGON', 'MULTIPOLYGON')
          AND ST_IsValid(geom)
        """

    def _build_union_query(self, target_table, assessment_table, output_table):
        """
        Build query for union operation - combines all geometries into single geometry

        Uses ST_Union to dissolve boundaries and create a single merged geometry
        from both target and assessment layers.

        Args:
            target_table: Name of target layer table
            assessment_table: Name of assessment layer table
            output_table: Name of output table

        Returns:
            str: SQL query
        """
        return f"""
        CREATE TABLE {output_table} AS
        WITH all_geometries AS (
            SELECT
                i.id AS input_id,
                NULL::integer AS identity_id,
                i.geom,
                'target' AS source_type
            FROM {target_table} i
            WHERE ST_IsValid(i.geom)
            UNION ALL
            SELECT
                NULL::integer AS input_id,
                n.id AS identity_id,
                n.geom,
                'assessment' AS source_type
            FROM {assessment_table} n
            WHERE ST_IsValid(n.geom)
        ),
        unioned AS (
            SELECT
                ST_Union(geom) AS geom
            FROM all_geometries
        )
        SELECT
            1 AS gid,
            NULL::integer AS input_id,
            NULL::integer AS identity_id,
            geom,
            'union' AS split_type,
            ST_Area(geom) AS shape_area,
            ST_Perimeter(geom) AS shape_length
        FROM unioned
        WHERE GeometryType(geom) IN ('POLYGON', 'MULTIPOLYGON')
          AND ST_IsValid(geom)
        """

    def _build_both_query(self, target_table, assessment_table, output_table):
        """
        Build query for both intersection and non-intersection operations

        This is the main query that combines:
        1. Intersected geometries (split by assessment layer)
        2. Non-intersected geometries (features that don't overlap)

        Args:
            target_table: Name of target layer table
            assessment_table: Name of assessment layer table
            output_table: Name of output table

        Returns:
            str: SQL query
        """
        return f"""
        CREATE TABLE {output_table} AS
        WITH intersected AS (
            SELECT
                i.id AS input_id,
                n.id AS identity_id,
                ST_Intersection(i.geom, n.geom) AS geom,
                'intersect' AS split_type
            FROM {target_table} i
            JOIN {assessment_table} n
              ON i.geom && n.geom
             AND ST_Intersects(i.geom, n.geom)
            WHERE ST_IsValid(i.geom)
              AND ST_IsValid(n.geom)
        ),
        filtered_intersected AS (
            SELECT *
            FROM intersected
            WHERE GeometryType(geom) IN ('POLYGON', 'MULTIPOLYGON')
              AND ST_IsValid(geom)
        ),
        non_intersected AS (
            SELECT
                i.id AS input_id,
                NULL::integer AS identity_id,
                i.geom,
                'no_overlap' AS split_type
            FROM {target_table} i
            LEFT JOIN {assessment_table} n
              ON i.geom && n.geom
             AND ST_Intersects(i.geom, n.geom)
            WHERE n.id IS NULL
              AND ST_IsValid(i.geom)
        )
        SELECT
            ROW_NUMBER() OVER () AS gid,
            input_id,
            identity_id,
            geom,
            split_type,
            ST_Area(geom) AS shape_area,
            ST_Perimeter(geom) AS shape_length
        FROM (
            SELECT * FROM filtered_intersected
            UNION ALL
            SELECT * FROM non_intersected
        ) combined
        """

    def cumulative_overlay(self, target_table, assessment_tables, output_table, layer_name=None):
        """
        Perform a cumulative overlay of target_table with each table in assessment_tables.

        Each assessment layer progressively splits the geometry fragments produced by the
        previous step, preserving all boundaries. The result is a single output layer
        whose features carry the original target id (input_id) plus the id of the
        assessment feature that covers each fragment (a0_id, a1_id, … or NULL when
        no assessment feature overlaps that fragment).

        Args:
            target_table: Name of the starting (target) PostgreSQL table
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
            # Step 0: seed the working table from the target selection
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

                cursor.execute(f"""
                    CREATE TEMP TABLE _overlay_next AS
                    WITH intersected AS (
                        SELECT
                            c.input_id,
                            {prior_select}a.id AS {col_name},
                            ST_Intersection(c.geom, a.geom) AS geom
                        FROM _overlay_current c
                        JOIN {assessment_table} a
                          ON c.geom && a.geom
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
                            {prior_select}NULL::integer AS {col_name},
                            c.geom
                        FROM _overlay_current c
                        LEFT JOIN {assessment_table} a
                          ON c.geom && a.geom
                         AND ST_Intersects(c.geom, a.geom)
                        WHERE a.id IS NULL
                          AND ST_IsValid(c.geom)
                    )
                    SELECT * FROM filtered
                    UNION ALL
                    SELECT * FROM remainder
                """)

                cursor.execute("DROP TABLE _overlay_current")
                cursor.execute("ALTER TABLE _overlay_next RENAME TO _overlay_current")

            # Build the final output table
            id_col_list = (", ".join(id_columns) + ", ") if id_columns else ""

            cursor.execute(f"""
                CREATE TABLE {output_table} AS
                SELECT
                    ROW_NUMBER() OVER () AS gid,
                    input_id,
                    {id_col_list}geom,
                    ST_Area(geom)      AS shape_area,
                    ST_Perimeter(geom) AS shape_length
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

    def get_analysis_summary(self, output_table):
        """
        Get detailed summary of analysis results

        Args:
            output_table: Name of output table to analyze

        Returns:
            dict: Summary statistics including counts by type, total area, etc.
        """
        if not self.db_manager.table_exists(output_table):
            raise Exception(f"Output table '{output_table}' does not exist")

        cursor = self.db_manager.connection.cursor()

        try:
            # Get overall statistics
            cursor.execute(f"""
                SELECT
                    COUNT(*) AS total_features,
                    SUM(shape_area) AS total_area,
                    AVG(shape_area) AS avg_area,
                    MAX(shape_area) AS max_area,
                    MIN(shape_area) AS min_area
                FROM {output_table}
            """)

            overall_stats = cursor.fetchone()

            # Get statistics by split type
            cursor.execute(f"""
                SELECT
                    split_type,
                    COUNT(*) AS count,
                    SUM(shape_area) AS total_area,
                    AVG(shape_area) AS avg_area
                FROM {output_table}
                GROUP BY split_type
            """)

            type_stats = {}
            for row in cursor.fetchall():
                type_stats[row[0]] = {
                    'count': row[1],
                    'total_area': row[2],
                    'avg_area': row[3]
                }

            cursor.close()

            return {
                'total_features': overall_stats[0],
                'total_area': overall_stats[1],
                'avg_area': overall_stats[2],
                'max_area': overall_stats[3],
                'min_area': overall_stats[4],
                'by_type': type_stats
            }

        except Exception as e:
            cursor.close()
            raise Exception(f"Failed to get analysis summary: {str(e)}")

    def validate_geometry_compatibility(self, target_table, assessment_table):
        """
        Validate that both tables have compatible geometry types for spatial analysis

        Args:
            target_table: Name of target layer table
            assessment_table: Name of assessment layer table

        Returns:
            dict: Validation results with compatibility status and geometry types

        Raises:
            Exception: If validation query fails
        """
        cursor = self.db_manager.connection.cursor()

        try:
            # Get geometry type of target table
            cursor.execute(f"""
                SELECT type, srid
                FROM geometry_columns
                WHERE f_table_schema = 'public'
                AND f_table_name = %s
                AND f_geometry_column = 'geom'
            """, (target_table,))

            target_result = cursor.fetchone()
            if not target_result:
                raise Exception(f"No geometry column found for table '{target_table}'")

            target_type, target_srid = target_result

            # Get geometry type of assessment table
            cursor.execute(f"""
                SELECT type, srid
                FROM geometry_columns
                WHERE f_table_schema = 'public'
                AND f_table_name = %s
                AND f_geometry_column = 'geom'
            """, (assessment_table,))

            assessment_result = cursor.fetchone()
            if not assessment_result:
                raise Exception(f"No geometry column found for table '{assessment_table}'")

            assessment_type, assessment_srid = assessment_result

            cursor.close()

            # Check SRID compatibility
            srid_compatible = (target_srid == assessment_srid)

            # Check geometry type compatibility
            # For intersection, both should be polygon-based
            polygon_types = ['POLYGON', 'MULTIPOLYGON']
            type_compatible = (
                target_type.upper() in polygon_types and
                assessment_type.upper() in polygon_types
            )

            return {
                'compatible': srid_compatible and type_compatible,
                'srid_compatible': srid_compatible,
                'type_compatible': type_compatible,
                'target_type': target_type,
                'target_srid': target_srid,
                'assessment_type': assessment_type,
                'assessment_srid': assessment_srid,
                'message': self._get_compatibility_message(
                    srid_compatible, type_compatible,
                    target_type, assessment_type,
                    target_srid, assessment_srid
                )
            }

        except Exception as e:
            cursor.close()
            raise Exception(f"Geometry validation failed: {str(e)}")

    def _get_compatibility_message(self, srid_compatible, type_compatible,
                                   target_type, assessment_type,
                                   target_srid, assessment_srid):
        """
        Generate user-friendly compatibility message

        Args:
            srid_compatible: Whether SRIDs match
            type_compatible: Whether geometry types are compatible
            target_type: Geometry type of target layer
            assessment_type: Geometry type of assessment layer
            target_srid: SRID of target layer
            assessment_srid: SRID of assessment layer

        Returns:
            str: Compatibility message
        """
        if srid_compatible and type_compatible:
            return "Layers are compatible for spatial analysis"

        messages = []

        if not srid_compatible:
            messages.append(
                f"SRID mismatch: Target ({target_srid}) vs Assessment ({assessment_srid}). "
                "Layers must have the same coordinate reference system."
            )

        if not type_compatible:
            messages.append(
                f"Geometry type incompatibility: Target ({target_type}) and/or "
                f"Assessment ({assessment_type}) are not polygon-based. "
                "Both layers must be POLYGON or MULTIPOLYGON for intersection analysis."
            )

        return " ".join(messages)
