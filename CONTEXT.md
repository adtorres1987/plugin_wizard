# CONTEXT.md — Q Assessment Wizard Plugin

## Project Summary

QGIS 3.0+ plugin that provides a multi-step wizard interface for creating spatial assessments. Users select layers, pick features interactively on a map, and run PostGIS spatial analysis (intersection/union). Results are added back as new QGIS layers.

---

## Directory Structure

```
plugin_wizard/
├── __init__.py                          # classFactory() entry point
├── plugin_wizard.py                     # QassessmentWizard — toolbar/menu integration
├── plugin_wizard_dialog.py              # Main dialog (~1,900 lines): wizard + map tools
├── plugin_wizard_dialog_base.ui         # Qt Designer UI (do not edit manually)
├── database_manager.py                  # PostgreSQL/PostGIS connectivity via psycopg2
├── spatial_analysis.py                  # PostGIS intersection/union queries
├── resources.py                         # Auto-generated from resources.qrc (pyrcc5)
├── resources.qrc                        # Qt resource manifest (icon.png)
├── metadata.txt                         # Plugin version, author, QGIS min version
├── Makefile                             # Build targets: compile, test, deploy, zip, etc.
├── pb_tool.cfg                          # Plugin Builder deploy config
├── plugin_upload.py                     # Upload script to QGIS plugin repository
├── icon.png                             # Toolbar icon
├── pylintrc                             # Pylint config
├── .vscode/launch.json                  # VS Code debugpy attach (port 5678)
├── i18n/af.ts                           # Afrikaans translation source
├── scripts/                             # Shell helpers: compile-strings, update-strings
├── help/                                # Sphinx documentation source
└── test/
    ├── qgis_interface.py                # Mock QgisInterface for testing
    ├── utilities.py                     # get_qgis_app() QGIS test setup
    ├── test_init.py                     # Validates metadata.txt required fields
    ├── test_resources.py                # Tests icon.png loads correctly
    ├── test_assessment_wizard_dialog.py
    ├── test_qgis_environment.py
    └── test_translations.py
```

---

## Plugin Metadata (`metadata.txt`)

| Field | Value |
|---|---|
| name | Plugin Wizard |
| qgisMinimumVersion | 3.0 |
| version | 0.1 |
| author | David Torres |
| email | davidt1987@gmail.com |
| repository | https://github.com/adtorres1987/q_plugin_wizard.git |
| icon | icon.png |
| experimental | False |

---

## Module Reference

### `__init__.py`
```python
def classFactory(iface):
    from .plugin_wizard import QpluginWizard
    return QpluginWizard(iface)
```

---

### `plugin_wizard.py` — `QassessmentWizard`

Main plugin class registered with QGIS.

| Method | Purpose |
|---|---|
| `__init__(iface)` | Init translator, plugin_dir, first_start flag |
| `tr(message)` | Qt translation helper |
| `add_action(...)` | Add toolbar button + menu action |
| `initGui()` | Register toolbar/menu on QGIS startup |
| `unload()` | Remove toolbar/menu on plugin unload |
| `run()` | Create and show `QassessmentWizardDialog` |

**Key attributes:** `self.iface`, `self.plugin_dir`, `self.actions`, `self.menu`, `self.first_start`

---

### `database_manager.py` — `DatabaseManager`

Handles PostgreSQL/PostGIS operations. `psycopg2` is optional — checked at import time via `PSYCOPG2_AVAILABLE` flag.

**Constructor defaults:** `host="localhost"`, `database="wizard_db"`, `user="postgres"`, `password="user123"`, `port="5432"`

| Method | Purpose |
|---|---|
| `connect()` | Open psycopg2 connection, enable PostGIS |
| `disconnect()` | Close connection |
| `sanitize_table_name(layer_name)` | Convert layer name → valid PG table name |
| `get_postgres_type_from_qgis_field(field)` | Map QGIS field type → PostgreSQL type |
| `convert_qvariant_to_python(value)` | QVariant → Python native type |
| `get_geometry_type_for_postgres(layer)` | QGIS layer → PostGIS geometry type string |
| `table_exists(table_name)` | Check table existence |
| `validate_geometry_type(table_name, expected_type)` | Assert geometry type matches |
| `drop_table(table_name)` | DROP TABLE |
| `create_table(table_name, layer)` | CREATE TABLE mirroring QGIS layer schema |
| `create_spatial_index(table_name)` | CREATE INDEX on geom column |
| `create_id_index(table_name)` | CREATE INDEX on id column |
| `get_existing_records(table_name)` | SELECT all rows |
| `migrate_layer(layer, table_name=None, progress_callback=None)` | Full layer → PG migration |
| `migrate_layers(layers_dict, progress_callback=None)` | Migrate multiple layers dict |

**Generated DDL pattern:**
```sql
CREATE TABLE {table_name} (
    id SERIAL PRIMARY KEY,
    {field_name} {field_type},
    ...
    geom GEOMETRY({geometry_type}, {srid})
)
```

---

### `spatial_analysis.py` — `SpatialAnalyzer`

Runs PostGIS queries and returns results as QGIS layers.

**Enum `OperationType`:** `INTERSECT`, `UNION`, `BOTH`

| Method | Purpose |
|---|---|
| `__init__(db_manager)` | Init with a `DatabaseManager` instance |
| `analyze_and_create_layer(target_table, assessment_table, output_table, layer_name, operation_type)` | Main entry point: run query, return QGIS layer |
| `_create_qgis_layer(table_name, layer_name)` | Wrap PG table as `QgsVectorLayer` |
| `_build_intersect_query(...)` | Generate ST_Intersection CTE query |
| `_build_union_query(...)` | Generate ST_Union query |
| `_build_both_query(...)` | Generate combined query |
| `get_analysis_summary(output_table)` | Stats: row count, area totals |
| `validate_geometry_compatibility(target_table, assessment_table)` | Pre-flight geometry check |

**Intersection query skeleton:**
```sql
CREATE TABLE {output_table} AS
WITH intersected AS (
    SELECT i.id AS input_id, n.id AS identity_id,
           ST_Intersection(i.geom, n.geom) AS geom, 'intersect' AS split_type
    FROM {target_table} i
    JOIN {assessment_table} n
      ON i.geom && n.geom AND ST_Intersects(i.geom, n.geom)
    WHERE ST_IsValid(i.geom) AND ST_IsValid(n.geom)
)
SELECT ROW_NUMBER() OVER () AS gid, input_id, identity_id, geom, split_type,
       ST_Area(geom) AS shape_area, ST_Perimeter(geom) AS shape_length
FROM intersected
WHERE GeometryType(geom) IN ('POLYGON', 'MULTIPOLYGON') AND ST_IsValid(geom)
```

---

### `plugin_wizard_dialog.py`

#### `FeatureSelectionTool(QgsMapTool)`

Point-click selection with CRS transformation.

| Method | Purpose |
|---|---|
| `__init__(canvas, layer, selection_callback)` | Init tool |
| `create_layer_from_feature_id(source_layer, feature_ids)` | Build memory layer from IDs |
| `canvasReleaseEvent(event)` | Click → transform coords → select feature |

#### `RectangleSelectTool(QgsMapTool)`

Rubber-band rectangle selection.

| Method | Purpose |
|---|---|
| `__init__(canvas, target_layer, selection_callback)` | Init |
| `canvasPressEvent(event)` | Start rubber band |
| `canvasMoveEvent(event)` | Update rubber band |
| `canvasReleaseEvent(event)` | Finalize selection |
| `update_rubber_band()` | Redraw rectangle |
| `deactivate()` | Cleanup |

#### `QassessmentWizardDialog(QWizard)`

**Layer status constants:**
```python
STATUS_INCLUDE = "Include in assessment"
STATUS_TARGET  = "Include as Target"
STATUS_SPATIAL_MARKER = "Spatial Marker"
STATUS_DO_NOT_INCLUDE = "Do not include"
```

| Method | Purpose |
|---|---|
| `__init__(parent, iface)` | Init wizard |
| `reject() / accept()` | Cancel / finish with spatial analysis |
| `cleanup_wizard_data()` | Release resources on close |
| `initialize_page_1/2/3()` | Page setup |
| `populate_layers()` | Fill layer table from QGIS project |
| `on_status_changed(text)` | Enforce single target layer |
| `migrate_selected_layers_to_postgres()` | Trigger DB migration |
| `validate_page_1/2()` | Gate page navigation |
| `get_target_layer()` | Return target layer from page 1 |
| `select_all_features()` | Select all features in target layer |
| `clear_selection()` | Deselect all |
| `update_selection_count()` | Update count label |
| `setup_page_2/3()` | Configure page content |
| `create_osm_layer()` | Add XYZ OSM tile layer |
| `detect_assessment_complexity()` | Simple vs complex assessment |
| `get_assessment_summary()` | Human-readable summary |
| `zoom_in/out()` | Canvas zoom |
| `zoom_to_layer()` | Fit canvas to layer extent |
| `toggle_pan()` | Activate pan tool |
| `toggle_rectangle_select()` | Activate rectangle select |

---

## UI Layout (`plugin_wizard_dialog_base.ui`)

**Root:** `QWizard` (1000×700, ModernStyle, title "Assessment Wizard")

### Page 1 — Initial Configuration
| Widget | Type | Purpose |
|---|---|---|
| `lineEdit_name` | QLineEdit | Assessment name |
| `textEdit_description` | QTextEdit | Assessment description |
| `tableWidget_layers` | QTableWidget | Layer list (Name, Geometry Type, Status) |

Status dropdown values per row: `Do not include`, `Include in assessment`, `Include as Target`, `Spatial Marker`

### Page 2 — Feature Selection
| Widget | Type | Purpose |
|---|---|---|
| `label_target_layer_name` | QLabel | Shows selected target layer name |
| `pushButton_select_all` | QPushButton | Select all features |
| `pushButton_clear_selection` | QPushButton | Clear selection |
| `label_selection_count` | QLabel | Live selected count |
| `map_canvas_container` | QWidget | Hosts `QgsMapCanvas` |
| `verticalLayout_map` | QVBoxLayout | Toolbar + canvas layout |

### Page 3 — Summary
| Widget | Type | Purpose |
|---|---|---|
| `label_summary_name/description/features` | QLabel | Assessment info |
| `label_complexity_summary` | QLabel | Simple or Complex assessment |
| `tableWidget_summary_layers` | QTableWidget | Included layers list |
| `widget_map_container_page3` | QWidget | Preview map of selected features |

---

## Wizard Workflow

```
Page 1: Layer Configuration
  ├─ Enter name + description
  ├─ Assign status to each project layer
  ├─ Validate: name not empty, exactly one TARGET layer,
  │            assessment layers share geometry type with target
  └─ On pass: migrate selected layers to PostgreSQL
        ↓
Page 2: Feature Selection
  ├─ Map canvas with OSM base + target layer (EPSG:3857)
  ├─ Click/rectangle select features
  ├─ Validate: ≥1 feature selected
  └─ Selected features stored in memory layer
        ↓
Page 3: Summary
  ├─ Review name, description, complexity, layer list
  ├─ Preview map
  └─ Finish:
       • Simple (target only) → memory layer added to project
       • Complex (+ assessment layers) → ST_Intersection/ST_Union
         executed in PostGIS → result tables added as QGIS layers
```

---

## Imports Summary

### `plugin_wizard_dialog.py`
```python
import os
from qgis.PyQt import uic, QtWidgets
from qgis.PyQt.QtWidgets import (QComboBox, QTableWidgetItem, QToolBar,
    QPushButton, QVBoxLayout, QMessageBox, QProgressDialog)
from qgis.PyQt.QtCore import Qt, QSize, QCoreApplication
from qgis.core import (QgsProject, QgsVectorLayer, QgsPointXY, QgsGeometry,
    QgsWkbTypes, QgsRectangle, QgsRasterLayer, QgsCoordinateReferenceSystem,
    QgsCoordinateTransform)
from qgis.gui import QgsMapCanvas, QgsMapTool, QgsMapToolPan, QgsRubberBand
from PyQt5.QtGui import QColor
from .database_manager import DatabaseManager, PSYCOPG2_AVAILABLE
from .spatial_analysis import SpatialAnalyzer, OperationType
```

### `database_manager.py`
```python
import re
from qgis.core import QgsWkbTypes
from PyQt5.QtCore import QVariant
try:
    import psycopg2
    from psycopg2 import sql
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False
```

### `spatial_analysis.py`
```python
from enum import Enum
from qgis.core import QgsVectorLayer, QgsProject, QgsDataSourceUri, QgsWkbTypes
```

---

## Key Design Decisions

| Decision | Rationale |
|---|---|
| `psycopg2` optional with `PSYCOPG2_AVAILABLE` flag | Plugin degrades gracefully if psycopg2 not installed |
| `qgis.PyQt` imports (not direct `PyQt5`) | Ensures compatibility with QGIS bundled Qt version |
| EPSG:3857 for map canvases | Required for OpenStreetMap XYZ tile compatibility |
| `QgsCoordinateTransform` in selection tools | Handles CRS mismatch between canvas and layer |
| `ST_IsValid()` filter in PostGIS queries | Prevents topology errors from invalid geometries |
| WKT serialization for geometry insert | Portable geometry format for psycopg2 parameter binding |
| Memory layer for selected features | No persistent storage needed for intermediate selection |

---

## Testing

Test files live in `test/`. Require a running QGIS environment (`QGIS_PREFIX_PATH` set).

```bash
make test                              # Full suite via nosetests
cd test && python -m pytest test_resources.py -v  # Single file
```

`test/utilities.py::get_qgis_app()` returns `(QGIS_APP, CANVAS, IFACE, PARENT)` for tests.
`test/qgis_interface.py` provides a stub `QgisInterface` that avoids needing a full QGIS session.

---

## Build Reference

```bash
make compile    # pyrcc5 -o resources.py resources.qrc
make test       # Run test suite
make deploy     # Copy plugin to QGIS plugins directory
make zip        # Build distribution ZIP
make doc        # Build Sphinx HTML docs
make pylint     # Lint checks
make pep8       # Style checks
```

Manual resource compile: `pyrcc5 -o resources.py resources.qrc`

Debug in VS Code: attach debugpy to QGIS on `127.0.0.1:5678` using `.vscode/launch.json`.
