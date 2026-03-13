"""
Database Manager Module
Handles migration of QGIS layers to a SpatiaLite database
"""

import os
import re
import sqlite3
import sys

from qgis.core import QgsWkbTypes
from PyQt5.QtCore import QVariant


def _load_spatialite(conn):
    """Load the mod_spatialite extension into a sqlite3 connection."""
    conn.enable_load_extension(True)

    candidates = ['mod_spatialite']

    if sys.platform == 'darwin':
        # Explicit versioned + unversioned paths for standard QGIS macOS installs
        mac_paths = [
            "/Applications/QGIS.app/Contents/MacOS/lib/mod_spatialite.7.so",
            "/Applications/QGIS.app/Contents/MacOS/lib/mod_spatialite.so",
            "/Applications/QGIS-LTR.app/Contents/MacOS/lib/mod_spatialite.7.so",
            "/Applications/QGIS-LTR.app/Contents/MacOS/lib/mod_spatialite.so",
        ]
        # Dynamically resolve the running QGIS bundle
        try:
            from qgis.PyQt.QtCore import QCoreApplication
            app_dir = QCoreApplication.applicationDirPath()
            lib_dir = os.path.join(app_dir, 'lib')
            mac_paths = [
                os.path.join(lib_dir, 'mod_spatialite.7.so'),
                os.path.join(lib_dir, 'mod_spatialite.so'),
            ] + mac_paths
        except Exception:
            pass
        candidates = mac_paths + candidates

    errors = []
    for path in candidates:
        try:
            conn.load_extension(path)
            return
        except Exception as e:
            errors.append(f"{path}: {e}")
            continue

    raise Exception(
        "Could not load SpatiaLite extension (mod_spatialite).\n"
        + "\n".join(errors)
    )


class DatabaseManager:
    """
    Manages database operations for QGIS layer migration to SpatiaLite
    """

    def __init__(self, db_path):
        """
        Initialize database manager with path to the SpatiaLite file.

        Args:
            db_path: Absolute path to the .db / .sqlite file (created if missing)
        """
        self.db_path = db_path
        self.connection = None

    def connect(self):
        """
        Open the SpatiaLite database and load the spatialite extension.
        Initialises spatial metadata if this is a new (empty) file.
        """
        try:
            is_new = not os.path.exists(self.db_path) or os.path.getsize(self.db_path) == 0
            self.connection = sqlite3.connect(self.db_path)
            self.connection.row_factory = sqlite3.Row
            _load_spatialite(self.connection)
            if is_new:
                self.connection.execute("SELECT InitSpatialMetaData(1)")
                self.connection.commit()
            return self.connection
        except Exception as e:
            raise Exception(f"Failed to open SpatiaLite database: {str(e)}")

    def disconnect(self):
        """Close the database connection."""
        if self.connection:
            self.connection.close()
            self.connection = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def sanitize_table_name(self, layer_name):
        """Convert a layer name to a valid SQLite table name."""
        table_name = re.sub(r'[^a-zA-Z0-9_]', '_', layer_name)
        table_name = table_name.lower()
        table_name = re.sub(r'_+', '_', table_name)
        table_name = table_name.strip('_')
        if table_name and table_name[0].isdigit():
            table_name = 'layer_' + table_name
        return table_name if table_name else 'unnamed_layer'

    def get_sqlite_type_from_qgis_field(self, field):
        """Map a QGIS field type to a SQLite column type."""
        type_name = field.typeName().upper()
        type_mapping = {
            'INTEGER':   'INTEGER',
            'INTEGER64': 'INTEGER',
            'REAL':      'REAL',
            'DOUBLE':    'REAL',
            'STRING':    'TEXT',
            'DATE':      'TEXT',
            'TIME':      'TEXT',
            'DATETIME':  'TEXT',
            'BOOL':      'INTEGER',
            'BINARY':    'BLOB',
        }
        return type_mapping.get(type_name, 'TEXT')

    def convert_qvariant_to_python(self, value):
        """Convert a QVariant to a native Python type."""
        if value is None:
            return None
        try:
            if isinstance(value, QVariant):
                return None if value.isNull() else value.value()
            return value
        except Exception:
            return value

    def get_geometry_type_for_spatialite(self, layer):
        """Get the SpatiaLite geometry type string for a QGIS layer."""
        wkb_type = layer.wkbType()
        geom_type_name = QgsWkbTypes.displayString(wkb_type)
        type_mapping = {
            'Point':            'POINT',
            'MultiPoint':       'MULTIPOINT',
            'LineString':       'LINESTRING',
            'MultiLineString':  'MULTILINESTRING',
            'Polygon':          'POLYGON',
            'MultiPolygon':     'MULTIPOLYGON',
            'PointZ':           'POINTZ',
            'MultiPointZ':      'MULTIPOINTZ',
            'LineStringZ':      'LINESTRINGZ',
            'MultiLineStringZ': 'MULTILINESTRINGZ',
            'PolygonZ':         'POLYGONZ',
            'MultiPolygonZ':    'MULTIPOLYGONZ',
        }
        return type_mapping.get(geom_type_name, 'GEOMETRY')

    # ------------------------------------------------------------------
    # Table operations
    # ------------------------------------------------------------------

    def table_exists(self, table_name):
        """Return True if the table exists in the database."""
        cursor = self.connection.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,)
        )
        exists = cursor.fetchone()[0] > 0
        cursor.close()
        return exists

    def validate_geometry_type(self, table_name, expected_type):
        """
        Return True if the geometry type of an existing table matches expected_type,
        or if the table does not yet exist.
        """
        if not self.table_exists(table_name):
            return True
        cursor = self.connection.cursor()
        cursor.execute(
            "SELECT type FROM geometry_columns WHERE f_table_name = ? AND f_geometry_column = 'geom'",
            (table_name,)
        )
        row = cursor.fetchone()
        cursor.close()
        if row:
            return row[0].upper() == expected_type.upper()
        return True

    def drop_table(self, table_name):
        """Drop a table and remove its geometry metadata."""
        cursor = self.connection.cursor()
        # Clean up SpatiaLite metadata
        cursor.execute(
            "DELETE FROM geometry_columns WHERE f_table_name = ?", (table_name,)
        )
        # Drop associated spatial index virtual tables if they exist
        cursor.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
            (f"idx_{table_name}_geom",)
        )
        if cursor.fetchone()[0] > 0:
            cursor.execute(f"DROP TABLE IF EXISTS idx_{table_name}_geom")
        cursor.execute(f"DROP TABLE IF EXISTS {table_name}")
        self.connection.commit()
        cursor.close()

    def create_table(self, table_name, layer):
        """Create a SpatiaLite table with a schema matching the QGIS layer."""
        srid = layer.crs().postgisSrid()
        geometry_type = self.get_geometry_type_for_spatialite(layer)

        # Build attribute column list
        field_defs = []
        for field in layer.fields():
            field_name = field.name().lower()
            field_type = self.get_sqlite_type_from_qgis_field(field)
            field_defs.append(f"{field_name} {field_type}")

        field_defs_sql = (', ' + ', '.join(field_defs)) if field_defs else ''
        cursor = self.connection.cursor()
        cursor.execute(
            f"CREATE TABLE {table_name} (id INTEGER PRIMARY KEY{field_defs_sql})"
        )
        cursor.execute(
            f"SELECT AddGeometryColumn('{table_name}', 'geom', {srid}, '{geometry_type}', 2)"
        )
        self.connection.commit()
        cursor.close()

    def create_spatial_index(self, table_name):
        """Create a SpatiaLite rtree spatial index on the geom column."""
        cursor = self.connection.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
            (f"idx_{table_name}_geom",)
        )
        already_exists = cursor.fetchone()[0] > 0
        if not already_exists:
            try:
                cursor.execute(f"SELECT CreateSpatialIndex('{table_name}', 'geom')")
            except Exception as e:
                print(f"Note: Could not create spatial index for {table_name}: {e}")
        cursor.close()

    def create_id_index(self, table_name):
        """Create an index on the id column (skip if primary key already covers it)."""
        cursor = self.connection.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name=?",
            (f"{table_name}_id_idx",)
        )
        if cursor.fetchone()[0] == 0:
            try:
                cursor.execute(f"CREATE INDEX IF NOT EXISTS {table_name}_id_idx ON {table_name} (id)")
                self.connection.commit()
            except Exception as e:
                print(f"Note: Could not create id index for {table_name}: {e}")
        cursor.close()

    def get_existing_records(self, table_name):
        """Return a dict mapping id → (geom_wkt, attributes_tuple) for all rows."""
        cursor = self.connection.cursor()
        cursor.execute(f"PRAGMA table_info({table_name})")
        column_names = [
            row[1] for row in cursor.fetchall()
            if row[1] not in ('id', 'geom')
        ]
        columns_str = ', '.join(column_names) if column_names else ''
        select_cols = f"id, AsText(geom)" + (f", {columns_str}" if columns_str else '')
        cursor.execute(f"SELECT {select_cols} FROM {table_name}")
        existing = {}
        for row in cursor.fetchall():
            existing[row[0]] = (row[1], tuple(row[2:]))
        cursor.close()
        return existing

    # ------------------------------------------------------------------
    # Migration
    # ------------------------------------------------------------------

    def migrate_layer(self, layer, table_name=None, progress_callback=None):
        """
        Migrate a QGIS vector layer into the SpatiaLite database.

        Returns:
            dict: {'inserted', 'updated', 'unchanged', 'errors', 'table_name'}
        """
        if not self.connection:
            raise Exception("Not connected to database. Call connect() first.")

        if not table_name:
            table_name = self.sanitize_table_name(layer.name())

        stats = {'inserted': 0, 'updated': 0, 'unchanged': 0, 'errors': 0,
                 'table_name': table_name}

        try:
            expected_geom_type = self.get_geometry_type_for_spatialite(layer)
            table_exists = self.table_exists(table_name)

            if table_exists and not self.validate_geometry_type(table_name, expected_geom_type):
                self.drop_table(table_name)
                table_exists = False

            if not table_exists:
                self.create_table(table_name, layer)
                self.create_spatial_index(table_name)
                self.create_id_index(table_name)
            else:
                self.create_spatial_index(table_name)
                self.create_id_index(table_name)

            existing_records = self.get_existing_records(table_name) if table_exists else {}

            field_names = [field.name().lower() for field in layer.fields()]
            srid = layer.crs().postgisSrid()
            total_features = layer.featureCount()
            cursor = self.connection.cursor()

            for idx, feature in enumerate(layer.getFeatures()):
                try:
                    if progress_callback:
                        progress_callback(idx + 1, total_features,
                                          f"Processing feature {idx + 1}/{total_features}")

                    feature_id = feature.id()
                    geometry = feature.geometry()
                    if geometry.isNull():
                        stats['errors'] += 1
                        continue

                    geom_wkt = geometry.asWkt()
                    python_attrs = [self.convert_qvariant_to_python(a) for a in feature.attributes()]

                    if feature_id in existing_records:
                        existing_geom, existing_attrs = existing_records[feature_id]
                        if geom_wkt == existing_geom and tuple(python_attrs) == existing_attrs:
                            stats['unchanged'] += 1
                            continue

                        set_parts = [f"{fn} = ?" for fn in field_names]
                        set_parts.append("geom = GeomFromText(?, ?)")
                        update_vals = python_attrs + [geom_wkt, srid, feature_id]
                        cursor.execute(
                            f"UPDATE {table_name} SET {', '.join(set_parts)} WHERE id = ?",
                            update_vals
                        )
                        stats['updated'] += 1
                    else:
                        columns = ['id'] + field_names + ['geom']
                        placeholders = ['?'] * (len(field_names) + 1) + ['GeomFromText(?, ?)']
                        insert_vals = [feature_id] + python_attrs + [geom_wkt, srid]
                        cursor.execute(
                            f"INSERT INTO {table_name} ({', '.join(columns)}) "
                            f"VALUES ({', '.join(placeholders)})",
                            insert_vals
                        )
                        stats['inserted'] += 1

                except Exception as e:
                    print(f"Error processing feature {feature.id()}: {e}")
                    stats['errors'] += 1

            self.connection.commit()
            cursor.close()

        except Exception as e:
            raise Exception(f"Migration error: {str(e)}")

        return stats

    def migrate_layers(self, layers_dict, progress_callback=None):
        """Migrate multiple QGIS layers. Returns a list of stats dicts."""
        all_stats = []
        total_layers = len(layers_dict)
        for idx, (layer_name, layer) in enumerate(layers_dict.items()):
            try:
                if progress_callback:
                    progress_callback(idx, total_layers, layer_name,
                                      f"Migrating layer {idx + 1}/{total_layers}: {layer_name}")
                all_stats.append(self.migrate_layer(layer))
            except Exception as e:
                all_stats.append({
                    'table_name': self.sanitize_table_name(layer_name),
                    'error': str(e), 'inserted': 0, 'updated': 0,
                    'unchanged': 0, 'errors': 0
                })
        return all_stats
