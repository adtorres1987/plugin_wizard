"""
Database Manager Module
Handles migration of QGIS layers to PostgreSQL database
"""

import re
from qgis.core import QgsWkbTypes
from PyQt5.QtCore import QVariant

try:
    import psycopg2
    from psycopg2 import sql
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False


class DatabaseManager:
    """
    Manages database operations for QGIS layer migration to PostgreSQL
    """

    def __init__(self, host="localhost", database="wizard_db", user="postgres", password="user123", port="5432"):
        """
        Initialize database manager with connection parameters

        Args:
            host: Database host
            database: Database name
            user: Database user
            password: Database password
            port: Database port
        """
        self.host = host
        self.database = database
        self.user = user
        self.password = password
        self.port = port
        self.connection = None

    def connect(self):
        """
        Establish connection to PostgreSQL database and enable PostGIS extension

        Returns:
            psycopg2.connection: Database connection object

        Raises:
            Exception: If psycopg2 is not available or connection fails
        """
        if not PSYCOPG2_AVAILABLE:
            raise Exception("psycopg2 library is not installed. Please install it to use PostgreSQL migration.")

        try:
            self.connection = psycopg2.connect(
                host=self.host,
                database=self.database,
                user=self.user,
                password=self.password,
                port=self.port
            )

            # Enable PostGIS extension
            cursor = self.connection.cursor()
            cursor.execute("CREATE EXTENSION IF NOT EXISTS postgis")
            self.connection.commit()
            cursor.close()

            return self.connection

        except Exception as e:
            raise Exception(f"Failed to connect to database: {str(e)}")

    def disconnect(self):
        """Close database connection"""
        if self.connection:
            self.connection.close()
            self.connection = None

    def sanitize_table_name(self, layer_name):
        """
        Convert layer name to valid PostgreSQL table name

        Args:
            layer_name: Original layer name

        Returns:
            str: Sanitized table name (lowercase, alphanumeric + underscore)
        """
        # Replace spaces and special characters with underscores
        table_name = re.sub(r'[^a-zA-Z0-9_]', '_', layer_name)
        # Convert to lowercase
        table_name = table_name.lower()
        # Remove consecutive underscores
        table_name = re.sub(r'_+', '_', table_name)
        # Remove leading/trailing underscores
        table_name = table_name.strip('_')
        # Ensure it doesn't start with a number
        if table_name and table_name[0].isdigit():
            table_name = 'layer_' + table_name

        return table_name if table_name else 'unnamed_layer'

    def get_postgres_type_from_qgis_field(self, field):
        """
        Map QGIS field type to PostgreSQL data type

        Args:
            field: QgsField object

        Returns:
            str: PostgreSQL data type
        """
        type_name = field.typeName().upper()

        # Map QGIS types to PostgreSQL types
        type_mapping = {
            'INTEGER': 'INTEGER',
            'INTEGER64': 'BIGINT',
            'REAL': 'DOUBLE PRECISION',
            'DOUBLE': 'DOUBLE PRECISION',
            'STRING': f'VARCHAR({field.length() if field.length() > 0 else 255})',
            'DATE': 'DATE',
            'TIME': 'TIME',
            'DATETIME': 'TIMESTAMP',
            'BOOL': 'BOOLEAN',
            'BINARY': 'BYTEA',
        }

        return type_mapping.get(type_name, 'TEXT')

    def convert_qvariant_to_python(self, value):
        """
        Convert QVariant to native Python type

        Args:
            value: QVariant or Python value

        Returns:
            Python native type (None, int, float, str, bool, etc.)
        """
        if value is None:
            return None

        try:
            if isinstance(value, QVariant):
                if value.isNull():
                    return None
                return value.value()
            return value
        except:
            return value

    def get_geometry_type_for_postgres(self, layer):
        """
        Get PostGIS geometry type for a QGIS layer

        Args:
            layer: QgsVectorLayer

        Returns:
            str: PostGIS geometry type (e.g., 'POINT', 'MULTIPOLYGON', etc.)
        """
        wkb_type = layer.wkbType()
        geom_type_name = QgsWkbTypes.displayString(wkb_type)

        # Map QGIS geometry types to PostGIS types
        type_mapping = {
            'Point': 'POINT',
            'MultiPoint': 'MULTIPOINT',
            'LineString': 'LINESTRING',
            'MultiLineString': 'MULTILINESTRING',
            'Polygon': 'POLYGON',
            'MultiPolygon': 'MULTIPOLYGON',
            'PointZ': 'POINTZ',
            'MultiPointZ': 'MULTIPOINTZ',
            'LineStringZ': 'LINESTRINGZ',
            'MultiLineStringZ': 'MULTILINESTRINGZ',
            'PolygonZ': 'POLYGONZ',
            'MultiPolygonZ': 'MULTIPOLYGONZ',
        }

        return type_mapping.get(geom_type_name, 'GEOMETRY')

    def table_exists(self, table_name):
        """
        Check if table exists in database

        Args:
            table_name: Name of table to check

        Returns:
            bool: True if table exists, False otherwise
        """
        cursor = self.connection.cursor()
        cursor.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_schema = 'public'
                AND table_name = %s
            )
        """, (table_name,))
        exists = cursor.fetchone()[0]
        cursor.close()
        return exists

    def validate_geometry_type(self, table_name, expected_type):
        """
        Validate that table geometry type matches expected type

        Args:
            table_name: Name of table
            expected_type: Expected PostGIS geometry type

        Returns:
            bool: True if types match or table doesn't exist, False if mismatch
        """
        if not self.table_exists(table_name):
            return True

        cursor = self.connection.cursor()
        cursor.execute("""
            SELECT type FROM geometry_columns
            WHERE f_table_schema = 'public'
            AND f_table_name = %s
            AND f_geometry_column = 'geom'
        """, (table_name,))

        result = cursor.fetchone()
        cursor.close()

        if result:
            existing_type = result[0].upper()
            return existing_type == expected_type.upper()

        return True

    def drop_table(self, table_name):
        """
        Drop table from database

        Args:
            table_name: Name of table to drop
        """
        cursor = self.connection.cursor()
        cursor.execute(f"DROP TABLE IF EXISTS {table_name} CASCADE")
        self.connection.commit()
        cursor.close()

    def create_table(self, table_name, layer):
        """
        Create table with schema matching QGIS layer

        Args:
            table_name: Name of table to create
            layer: QgsVectorLayer to match schema from
        """
        cursor = self.connection.cursor()

        # Get SRID from layer
        srid = layer.crs().postgisSrid()

        # Get geometry type
        geometry_type = self.get_geometry_type_for_postgres(layer)

        # Build CREATE TABLE statement
        columns = []
        columns.append("id SERIAL PRIMARY KEY")

        # Add attribute fields
        for field in layer.fields():
            field_name = field.name().lower()
            field_type = self.get_postgres_type_from_qgis_field(field)
            columns.append(f"{field_name} {field_type}")

        # Add geometry column
        columns.append(f"geom GEOMETRY({geometry_type}, {srid})")

        create_statement = f"CREATE TABLE {table_name} ({', '.join(columns)})"
        cursor.execute(create_statement)
        self.connection.commit()
        cursor.close()

    def create_spatial_index(self, table_name):
        """
        Create spatial index on geometry column

        Args:
            table_name: Name of table

        Returns:
            bool: True if index created or already exists, False on error
        """
        cursor = self.connection.cursor()

        # Check if spatial index already exists
        cursor.execute("""
            SELECT COUNT(*)
            FROM pg_indexes
            WHERE schemaname = 'public'
            AND tablename = %s
            AND indexdef LIKE '%%USING gist%%geom%%'
        """, (table_name,))

        index_exists = cursor.fetchone()[0] > 0

        if not index_exists:
            try:
                cursor.execute(f"CREATE INDEX {table_name}_geom_idx ON {table_name} USING GIST (geom)")
                self.connection.commit()
                cursor.close()
                return True
            except Exception as e:
                print(f"Note: Could not create spatial index for {table_name}: {str(e)}")
                cursor.close()
                return False

        cursor.close()
        return True

    def create_id_index(self, table_name):
        """
        Create index on id column for faster lookups during validation

        Args:
            table_name: Name of table

        Returns:
            bool: True if index created or already exists, False on error
        """
        cursor = self.connection.cursor()

        # Check if id index already exists
        cursor.execute("""
            SELECT COUNT(*)
            FROM pg_indexes
            WHERE schemaname = 'public'
            AND tablename = %s
            AND indexdef LIKE '%%id%%'
            AND indexdef NOT LIKE '%%pkey%%'
        """, (table_name,))

        index_exists = cursor.fetchone()[0] > 0

        if not index_exists:
            try:
                # Note: SERIAL PRIMARY KEY already creates an index, but we'll check for it
                # Check if id column is primary key
                cursor.execute("""
                    SELECT COUNT(*)
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                        ON tc.constraint_name = kcu.constraint_name
                    WHERE tc.table_name = %s
                    AND tc.constraint_type = 'PRIMARY KEY'
                    AND kcu.column_name = 'id'
                """, (table_name,))

                has_primary_key = cursor.fetchone()[0] > 0

                if not has_primary_key:
                    # Create index if id is not primary key
                    cursor.execute(f"CREATE INDEX {table_name}_id_idx ON {table_name} (id)")
                    self.connection.commit()
                    cursor.close()
                    return True
                else:
                    # Primary key already provides index
                    cursor.close()
                    return True

            except Exception as e:
                print(f"Note: Could not create id index for {table_name}: {str(e)}")
                cursor.close()
                return False

        cursor.close()
        return True

    def get_existing_records(self, table_name):
        """
        Retrieve all existing records from table

        Args:
            table_name: Name of table

        Returns:
            dict: Dictionary mapping ID to (geometry_wkt, attributes_tuple)
        """
        cursor = self.connection.cursor()

        # Get column names (excluding id and geom)
        cursor.execute(f"""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = '{table_name}'
            AND column_name NOT IN ('id', 'geom')
            ORDER BY ordinal_position
        """)

        column_names = [row[0] for row in cursor.fetchall()]
        columns_str = ', '.join(column_names)

        # Get all records
        cursor.execute(f"SELECT id, ST_AsText(geom), {columns_str} FROM {table_name}")

        existing = {}
        for row in cursor.fetchall():
            record_id = row[0]
            geom_wkt = row[1]
            attributes = row[2:]
            existing[record_id] = (geom_wkt, attributes)

        cursor.close()
        return existing

    def migrate_layer(self, layer, table_name=None, progress_callback=None):
        """
        Migrate QGIS layer to PostgreSQL table

        Args:
            layer: QgsVectorLayer to migrate
            table_name: Optional table name (defaults to sanitized layer name)
            progress_callback: Optional callback function(current, total, status_message)

        Returns:
            dict: Statistics about migration (inserted, updated, unchanged, errors)
        """
        if not self.connection:
            raise Exception("Not connected to database. Call connect() first.")

        # Sanitize table name
        if not table_name:
            table_name = self.sanitize_table_name(layer.name())

        stats = {
            'inserted': 0,
            'updated': 0,
            'unchanged': 0,
            'errors': 0,
            'table_name': table_name
        }

        try:
            cursor = self.connection.cursor()

            # Get geometry type
            expected_geometry_type = self.get_geometry_type_for_postgres(layer)

            # Check if table exists and validate geometry type
            table_exists = self.table_exists(table_name)

            if table_exists:
                # Validate geometry type
                if not self.validate_geometry_type(table_name, expected_geometry_type):
                    # Drop and recreate table with correct geometry type
                    self.drop_table(table_name)
                    table_exists = False

            # Create table if it doesn't exist
            if not table_exists:
                self.create_table(table_name, layer)
                # Create indexes for new table
                self.create_spatial_index(table_name)
                self.create_id_index(table_name)
            else:
                # Ensure indexes exist for existing table
                self.create_spatial_index(table_name)
                self.create_id_index(table_name)

            # Get existing records if table existed
            existing_records = {}
            if table_exists:
                existing_records = self.get_existing_records(table_name)

            # Get field names (excluding id)
            field_names = [field.name().lower() for field in layer.fields()]

            # Iterate through features
            total_features = layer.featureCount()
            for idx, feature in enumerate(layer.getFeatures()):
                try:
                    if progress_callback:
                        progress_callback(idx + 1, total_features, f"Processing feature {idx + 1}/{total_features}")

                    feature_id = feature.id()
                    geometry = feature.geometry()

                    if geometry.isNull():
                        stats['errors'] += 1
                        continue

                    geom_wkt = geometry.asWkt()

                    # Get attributes and convert QVariants
                    attributes = feature.attributes()
                    python_attributes = [self.convert_qvariant_to_python(attr) for attr in attributes]

                    # Check if record exists and compare
                    if feature_id in existing_records:
                        existing_geom_wkt, existing_attrs = existing_records[feature_id]

                        # Compare geometry and attributes
                        if geom_wkt == existing_geom_wkt and tuple(python_attributes) == existing_attrs:
                            stats['unchanged'] += 1
                            continue

                        # Update existing record
                        update_parts = []
                        update_values = []

                        for field_name, value in zip(field_names, python_attributes):
                            update_parts.append(f"{field_name} = %s")
                            update_values.append(value)

                        update_parts.append("geom = ST_GeomFromText(%s, %s)")
                        update_values.append(geom_wkt)
                        update_values.append(layer.crs().postgisSrid())
                        update_values.append(feature_id)

                        update_statement = f"UPDATE {table_name} SET {', '.join(update_parts)} WHERE id = %s"
                        cursor.execute(update_statement, update_values)
                        stats['updated'] += 1
                    else:
                        # Insert new record
                        columns = ['id'] + field_names + ['geom']
                        placeholders = ['%s'] * (len(field_names) + 1) + ['ST_GeomFromText(%s, %s)']

                        insert_statement = f"INSERT INTO {table_name} ({', '.join(columns)}) VALUES ({', '.join(placeholders)})"
                        insert_values = [feature_id] + python_attributes + [geom_wkt, layer.crs().postgisSrid()]

                        cursor.execute(insert_statement, insert_values)
                        stats['inserted'] += 1

                except Exception as e:
                    print(f"Error processing feature {feature_id}: {str(e)}")
                    stats['errors'] += 1

            self.connection.commit()
            cursor.close()

        except Exception as e:
            raise Exception(f"Migration error: {str(e)}")

        return stats

    def migrate_layers(self, layers_dict, progress_callback=None):
        """
        Migrate multiple QGIS layers to PostgreSQL

        Args:
            layers_dict: Dictionary mapping layer names to QgsVectorLayer objects
            progress_callback: Optional callback function(layer_index, total_layers, layer_name, message)

        Returns:
            list: List of statistics dictionaries for each layer
        """
        all_stats = []
        total_layers = len(layers_dict)

        for idx, (layer_name, layer) in enumerate(layers_dict.items()):
            try:
                if progress_callback:
                    progress_callback(idx, total_layers, layer_name, f"Migrating layer {idx + 1}/{total_layers}: {layer_name}")

                stats = self.migrate_layer(layer)
                all_stats.append(stats)

            except Exception as e:
                all_stats.append({
                    'table_name': self.sanitize_table_name(layer_name),
                    'error': str(e),
                    'inserted': 0,
                    'updated': 0,
                    'unchanged': 0,
                    'errors': 0
                })

        return all_stats
