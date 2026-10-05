# OEM Threshold Extractor

Extracts equipment operating limits from OEM manual evidence and, when requested,
records validated limits in the project's `OEM_EQUIPMENT_THRESHOLDS` table for
SKU-aware diagnostics and Remaining Useful Life (RUL) prediction.

## When to use this skill

Use when the user needs to:
- Find maximum operating temperature or vibration limits in an OEM manual
- Validate an existing equipment or SKU threshold against its source
- Prepare OEM-backed thresholds for the project's RUL pipeline
- Investigate which manual evidence supports a limit or operating constraint

Trigger phrases: "extract OEM thresholds", "OEM temperature limit",
"vibration limit for this SKU", "verify equipment limits", "look up the OEM
manual", "update the threshold table".

## Project sources

- `data/OEM_Maintenance_and_Operations_Manual.pdf` is the local OEM manual.
- `OEE_COMMAND_CENTER.FACTORY_FLOOR.OEM_MANUAL_CHUNKS` stores parsed manual text
  with `FILE_NAME`, `CHUNK_INDEX`, and `CHUNK_TEXT`.
- `OEE_COMMAND_CENTER.FACTORY_FLOOR.OEM_MANUAL_SEARCH` is the configured Cortex
  Search service when available.
- `OEE_COMMAND_CENTER.FACTORY_FLOOR.OEM_EQUIPMENT_THRESHOLDS` stores limits in
  `EQUIPMENT_ID`, `SKU_ID`, `MAX_TEMP_LIMIT`, and `MAX_VIBRATION_LIMIT`.

The setup workflow seeds example/global values. Treat those as configured data,
not as proof that a value came from the OEM manual.

## Extraction workflow

1. Confirm the equipment identifier, model/type, SKU when applicable, and the
   units expected by the caller. Do not infer a machine model from an ID suffix
   unless the user or source confirms it.
2. Search `OEM_MANUAL_SEARCH` when available. If it is unavailable, inspect
   relevant rows in `OEM_MANUAL_CHUNKS`, for example:

   ```sql
   SELECT FILE_NAME, CHUNK_INDEX, CHUNK_TEXT
   FROM OEE_COMMAND_CENTER.FACTORY_FLOOR.OEM_MANUAL_CHUNKS
   WHERE CHUNK_TEXT ILIKE '%<equipment or model term>%'
      OR CHUNK_TEXT ILIKE '%<limit or metric term>%'
   LIMIT 20;
   ```

3. Extract only limits directly supported by the returned manual text. Capture
   the exact value, unit, metric, qualifiers (such as sustained, peak, ambient,
   or operating mode), and source file plus chunk index/page when available.
4. Check that temperature and vibration limits refer to the requested equipment
   and operating context. Convert units only when the conversion is exact and
   show both the source and converted units. If the manual is ambiguous or does
   not contain a limit, report that instead of filling in a plausible value.
5. Compare the evidence with the existing table row and report any conflict.
   Keep manual-derived evidence distinct from global defaults and setup seeds.
6. Do not write to Snowflake unless the user explicitly asks to save or update
   the threshold. When asked, use the existing table columns and equipment/SKU
   key; preserve other rows and verify the resulting row with a SELECT.
7. When thresholds are saved, continue with the `rul-prediction` skill to
   evaluate forecast breaches using the updated table values.

## Threshold resolution used by RUL

The project's `ASSET_RUL_PREDICTIONS` view resolves each active SKU's limits in
this order:

1. Exact `EQUIPMENT_ID` and `SKU_ID`
2. Exact equipment and `SKU_ID = 'ALL'`
3. `EQUIPMENT_ID = 'ALL'` and `SKU_ID = 'ALL'`
4. SQL fallback of 90.0 C and 2.3 mm/s when no matching value exists

Do not store an equipment-specific or SKU-specific manual limit under `ALL`
unless the user explicitly requests a global threshold.

## Response format

Return temperature and vibration limits separately, with units, operating
qualifiers, and a source citation. State whether the value was found, verified,
conflicted with the configured row, or was not established. Clearly say whether
the Snowflake table was changed.

## Related skills

- `it-ot-timeseries-joiner` establishes the project IT/OT production context.
- `rul-prediction` consumes the resolved thresholds and forecasts.