# IT/OT Time-Series Boundary Joiner

Executes a time-series boundary join across disparate-frequency datasets,
mapping high-velocity OT sensor timestamps inside the Start/End duration
blocks of low-velocity IT enterprise records.

## When to use this skill

Use when the user needs to:
- Join OT sensor telemetry with IT batch/schedule records on a shared asset key
- Align high-frequency sensor readings to low-frequency production windows
- Map OT timestamps into IT duration blocks (Start/End time ranges)
- Correlate equipment sensor data with production orders, batch schedules,
  or work-order intervals
- Investigate OEE degradation by fusing sensor metrics with schedule context

Trigger phrases: "join IT and OT", "align sensor data with batch schedule",
"boundary join", "map sensor timestamps to production windows",
"time-series join", "IT/OT join", "fuse OT telemetry with IT records".

## Parameters

| Parameter  | Type   | Required | Description |
|------------|--------|----------|-------------|
| `ot_table` | string | yes      | Fully-qualified name of the high-frequency OT sensor telemetry table. Must contain a `TIMESTAMP` column. |
| `it_table` | string | yes      | Fully-qualified name of the low-frequency IT batch/schedule table. Must contain `START_TIME` and `END_TIME` columns. |
| `join_key` | string | yes      | The shared asset identifier column present in both tables (e.g., `EQUIPMENT_ID`). |

## Validation checks

Before executing the join, verify:

1. **OT table exists** and contains a `TIMESTAMP` column (TIMESTAMP_NTZ or TIMESTAMP_LTZ).
2. **IT table exists** and contains both `START_TIME` and `END_TIME` columns.
3. **Join key exists** in both tables under the same column name.
4. **No NULL join keys** ΓÇö warn if either table has NULLs in the join key column,
   as those rows will silently drop from the result.
5. **Time ranges are valid** ΓÇö `START_TIME <= END_TIME` in the IT table;
   flag any inverted intervals.
6. **Timezone consistency** ΓÇö both timestamp columns should share the same
   timezone semantics to avoid silent misalignment.

## SQL logic

The core join is a LEFT JOIN with a BETWEEN predicate that maps each OT
sensor reading into the IT duration window that contains it:

```sql
SELECT
    ot.*,
    it.*
FROM {{ot_table}} ot
LEFT JOIN {{it_table}} it
    ON ot.{{join_key}} = it.{{join_key}}
    AND ot.TIMESTAMP BETWEEN it.START_TIME AND it.END_TIME;
```

Rows in the OT table that fall outside any IT window retain NULLs for the
IT columns (LEFT JOIN semantics).

## End-to-end workflow

For the factory health project, run this join as the production-context step,
then use the related skills:

1. Use `oem-threshold-extractor` to retrieve and cite equipment/SKU limits from
    the OEM manual. Save validated limits to the existing thresholds table only
    when requested.
2. Use `rul-prediction` to compare the available temperature and vibration
    forecasts with the active SKU's resolved limits.
3. Use the joined production window and SKU as context when explaining the
    predicted breach. Keep the general-purpose `ot_table`, `it_table`, and
    `join_key` parameters above unchanged for other datasets.

## Related skills

- `oem-threshold-extractor` validates OEM operating limits.
- `rul-prediction` evaluates forecast breaches against the resolved limits.

## Example usage

> "Join the OT sensor table `MFG.SENSORS.READINGS` with the IT batch
> schedule `MFG.ERP.PRODUCTION_ORDERS` on `EQUIPMENT_ID`."

Parameters:
- `ot_table`: `MFG.SENSORS.READINGS`
- `it_table`: `MFG.ERP.PRODUCTION_ORDERS`
- `join_key`: `EQUIPMENT_ID`

## Reference artifact

Source definition: `skills/IT_OT_TimeSeries_Joiner.yaml`
