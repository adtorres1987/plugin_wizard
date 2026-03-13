# Spatial Analysis Feature

## Overview
This module adds spatial analysis capabilities to the Assessment Wizard plugin. It performs intersection and union operations between a target layer and assessment layers in PostgreSQL/PostGIS, creating new result layers in QGIS.

## Files Added/Modified

### New Files
1. **spatial_analysis.py** - Main spatial analysis module
   - `SpatialAnalyzer` class: Handles spatial operations
   - `OperationType` enum: Defines operation types (INTERSECT, UNION, BOTH)

### Modified Files
1. **plugin_wizard_dialog.py**
   - Added import for `SpatialAnalyzer` and `OperationType`
   - Added `perform_spatial_analysis()` method
   - Added buttons in `initialize_page_3()` for migration and spatial analysis

2. **database_manager.py** (previously created)
   - Handles PostgreSQL migration operations

## How It Works

### Workflow
1. **Select Layers** (Page 1):
   - Mark one layer as "Include as Target"
   - Mark one or more layers as "Include in assessment"
   - Other layers can be marked as "Spatial Marker" or "Do not include"

2. **Select Features** (Page 2):
   - Select specific features from the target layer

3. **Summary & Analysis** (Page 3):
   - Click "Migrate to PostgreSQL" to migrate layers to database
   - Click "Perform Spatial Analysis" to execute the analysis

### Spatial Analysis Query Structure
The analysis uses the following PostgreSQL/PostGIS query structure:

```sql
CREATE TABLE {output_table} AS
WITH intersected AS (
    SELECT
        i.id AS input_id,
        n.id AS identity_id,
        ST_Intersection(i.geom, n.geom) AS geom,
        'intersect' AS split_type
    FROM {target_table} i
    JOIN {assessment_table} n
      ON ST_Intersects(i.geom, n.geom)
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
        NULL AS identity_id,
        i.geom,
        'no_overlap' AS split_type
    FROM {target_table} i
    LEFT JOIN {assessment_table} n
      ON ST_Intersects(i.geom, n.geom)
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
```

## Result Layer Structure

The output layer contains the following fields:
- `gid` (INTEGER): Unique identifier for each result feature
- `input_id` (INTEGER): ID from the target layer
- `identity_id` (INTEGER): ID from the assessment layer (NULL for non-intersected features)
- `geom` (GEOMETRY): The resulting geometry
- `split_type` (TEXT): Either 'intersect' or 'no_overlap'
- `shape_area` (DOUBLE PRECISION): Area of the geometry
- `shape_length` (DOUBLE PRECISION): Perimeter of the geometry

## Features

### Validation
- **Geometry Type Validation**: Ensures both layers are polygon-based (POLYGON or MULTIPOLYGON)
- **SRID Compatibility**: Verifies both layers use the same coordinate reference system
- **Table Existence**: Checks that layers have been migrated to PostgreSQL before analysis

### Operations
- **INTERSECT**: Only returns intersected portions of geometries
- **UNION**: Only returns non-intersecting portions from the target layer
- **BOTH**: Returns both intersected and non-intersected features (default)

### User Experience
- Progress dialogs during analysis
- Comprehensive error messages
- Automatic layer creation in QGIS project
- Summary statistics after completion

## Usage Example

### Step 1: Prepare Layers
```python
# In Page 1 of the wizard:
# - Select "landuse_a_free" → "Include as Target"
# - Select "natural_a_free" → "Include in assessment"
```

### Step 2: Migrate to PostgreSQL
```python
# Click "Migrate to PostgreSQL" button
# This creates tables: landuse_a_free, natural_a_free
```

### Step 3: Perform Analysis
```python
# Click "Perform Spatial Analysis" button
# This creates: landuse_a_free_vs_natural_a_free_analysis
```

### Result
- New layer added to QGIS: "landuse_a_free vs natural_a_free (Analysis)"
- Statistics shown:
  - Total features created
  - Number of intersected features
  - Number of non-intersected features

## API Reference

### SpatialAnalyzer Class

#### `__init__(db_manager)`
Initialize the analyzer with a DatabaseManager instance.

#### `analyze_and_create_layer(target_table, assessment_table, output_table, layer_name=None, operation_type=OperationType.BOTH)`
Perform spatial analysis and create a new QGIS layer.

**Parameters:**
- `target_table` (str): Name of target layer table in PostgreSQL
- `assessment_table` (str): Name of assessment layer table in PostgreSQL
- `output_table` (str): Name for the output table
- `layer_name` (str, optional): Display name for the QGIS layer
- `operation_type` (OperationType): Type of operation to perform

**Returns:**
- dict: Contains statistics and the created QgsVectorLayer

#### `validate_geometry_compatibility(target_table, assessment_table)`
Validate that layers are compatible for spatial analysis.

**Returns:**
- dict: Compatibility information including types, SRIDs, and validation messages

#### `get_analysis_summary(output_table)`
Get detailed statistics about analysis results.

**Returns:**
- dict: Summary including total features, areas, and statistics by type

## Database Requirements

- PostgreSQL 9.5+
- PostGIS 2.0+
- Tables must have:
  - `id` column (INTEGER)
  - `geom` column (GEOMETRY)
  - Valid geometry types (POLYGON or MULTIPOLYGON)

## Error Handling

The module handles various error scenarios:
- Database connection failures
- Missing tables
- Geometry incompatibility
- Invalid geometries (filtered out automatically)
- SRID mismatches
- User cancellation

## Future Enhancements

Potential improvements:
- Support for LINE and POINT geometry types
- Additional spatial operations (buffer, difference, symmetric difference)
- Batch analysis for multiple assessment layers
- Export results to various formats (GeoJSON, Shapefile)
- Custom attribute preservation from source layers
- Spatial index optimization hints
