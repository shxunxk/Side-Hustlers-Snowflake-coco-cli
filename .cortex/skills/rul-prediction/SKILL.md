# RUL Prediction

Interprets the project's temperature and vibration forecasts against the
active SKU's resolved OEM thresholds and reports the earliest predicted breach
as Remaining Useful Life (RUL).

## When to use this skill

Use when the user needs to:
- Check predicted failure timing for one or more equipment assets
- Explain an RUL value or its predictive cause
- Compare a 72-hour forecast with the current equipment/SKU thresholds
- Investigate missing RUL results or a changed OEM threshold

Trigger phrases: "predict RUL", "remaining useful life", "hours to failure",
"when will this equipment breach", "explain the RUL prediction",
"which forecast drives the alert".

## Project data path

The project aggregates `IT_OT_CONVERGED` into hourly temperature and vibration
history, forecasts each series 72 hours ahead, and stores the forecasts in
`PREDICTED_TEMPERATURES` and `PREDICTED_VIBRATIONS`. The
`ASSET_RUL_PREDICTIONS` view resolves active SKU thresholds from
`OEM_EQUIPMENT_THRESHOLDS`, finds the first forecast timestamp at or above each
metric's limit, and keeps the earliest breach per equipment.

Use these existing objects in `OEE_COMMAND_CENTER.FACTORY_FLOOR`; do not train
new models or recreate views unless the user explicitly requests a pipeline
change.

## Prediction workflow

1. Confirm the requested equipment ID and, if supplied, the relevant SKU. The
   view uses the most recent non-idle SKU from `IT_OT_CONVERGED` as the active
   SKU for threshold selection.
2. Read the existing prediction and threshold results:

   ```sql
   SELECT EQUIPMENT_ID, PREDICTED_FAILURE_TIMESTAMP, RUL_HOURS,
          PREDICTIVE_CAUSE
   FROM OEE_COMMAND_CENTER.FACTORY_FLOOR.ASSET_RUL_PREDICTIONS
   WHERE EQUIPMENT_ID = '<equipment_id>';
   ```

3. Explain which metric caused the result (`Temperature Forecast Breach` or
   `Vibration Forecast Breach`), the predicted failure timestamp, and the
   returned RUL in hours. The view selects the earliest breach across both
   forecast tables and rounds hours to one decimal with a minimum of 1 hour.
4. When explaining threshold selection, use the view's actual precedence:
   equipment+SKU, equipment+ALL, global ALL+ALL, then SQL defaults of 90.0 C
   and 2.3 mm/s. Do not substitute the manual's value unless it is present in
   the configured table or the user explicitly asks for a what-if comparison.
5. For a missing row, do not report RUL as zero or infinite. Check whether the
   equipment has a current non-idle row in `IT_OT_CONVERGED`, whether its
   prediction rows cover the expected forecast horizon, and whether any
   forecast value reaches the resolved threshold. Report which prerequisite is
   missing or state that no breach is forecast in the available horizon.
6. If the user asks to change a threshold, use `oem-threshold-extractor` to
   establish source evidence first. After an explicitly requested table update,
   query the existing RUL view again and report the new result; do not alter
   SQL, forecast models, or application code as part of this skill.
7. For IT/OT alignment or batch context, use `it-ot-timeseries-joiner` before
   interpreting which production window was active.

## Alert interpretation

The project's current action bands are:

| RUL | Action | Priority |
|---|---|---|
| 6 hours or less | `IMMEDIATE_MAINTENANCE` | `CRITICAL` |
| More than 6 and up to 24 hours | `SCHEDULE_MAINTENANCE` | `HIGH` |
| More than 24 hours | `MONITOR_EQUIPMENT` | `MEDIUM` |

These bands describe the current application behavior; they are not OEM limits.
Do not dispatch a Slack alert or write alert history unless the user requests
that action.

## Response format

Report equipment and active SKU, RUL hours, predicted failure timestamp,
predictive cause, resolved threshold values and their source tier when
available, and any data or freshness limitation. Distinguish a forecast result
from a confirmed equipment failure.

## Related skills

- `it-ot-timeseries-joiner` aligns sensor readings with production windows.
- `oem-threshold-extractor` establishes and validates OEM limits.