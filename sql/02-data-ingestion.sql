--run this in snowflake worksheet after the files are uploaded as data--
COPY INTO RAW_IT_BATCHES FROM @FACTORY_DATA_STAGE/it_batch_schedule.csv FILE_FORMAT = CSV_FORMAT FORCE = TRUE;
COPY INTO RAW_OT_TELEMETRY FROM @FACTORY_DATA_STAGE/ot_telemetry_stream.csv FILE_FORMAT = CSV_FORMAT FORCE = TRUE;

-- Refresh the dynamic table to ensure data is populated immediately for ML functions
ALTER DYNAMIC TABLE IT_OT_CONVERGED REFRESH;