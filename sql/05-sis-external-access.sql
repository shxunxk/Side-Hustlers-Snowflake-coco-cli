-- Slack Notification Setup for Streamlit in Snowflake
-- Run this ONCE as ACCOUNTADMIN before deploying the SiS app.
--
-- This script sets up Slack notifications via Snowflake's native
-- Webhook Notification Integration. No External Access Integration required.
--
-- PREREQUISITES:
--   1. Create a Slack Incoming Webhook at https://api.slack.com/apps
--   2. Extract the secret portion from the webhook URL:
--      URL:    https://hooks.slack.com/services/T.../B.../xxx
--      Secret: T.../B.../xxx  (everything after /services/)
--   3. Replace <YOUR_WEBHOOK_SECRET> below with your actual secret.

USE ROLE ACCOUNTADMIN;
USE DATABASE OEE_COMMAND_CENTER;
USE SCHEMA FACTORY_FLOOR;

-- 1. Secret holding the Slack webhook secret (the path portion after /services/)
-- IMPORTANT: Replace <YOUR_WEBHOOK_SECRET> with your actual value
CREATE OR REPLACE SECRET SLACK_WEBHOOK_SECRET
  TYPE = GENERIC_STRING
  SECRET_STRING = '<YOUR_WEBHOOK_SECRET>';

-- 2. Webhook Notification Integration (works on all account types including trial)
CREATE OR REPLACE NOTIFICATION INTEGRATION SLACK_WEBHOOK_INT
  TYPE = WEBHOOK
  ENABLED = TRUE
  WEBHOOK_URL = 'https://hooks.slack.com/services/SNOWFLAKE_WEBHOOK_SECRET'
  WEBHOOK_SECRET = OEE_COMMAND_CENTER.FACTORY_FLOOR.SLACK_WEBHOOK_SECRET
  WEBHOOK_BODY_TEMPLATE = '{"text": "SNOWFLAKE_WEBHOOK_MESSAGE"}'
  WEBHOOK_HEADERS = ('Content-Type'='application/json');

-- 3. Test the notification (uncomment to verify)
-- CALL SYSTEM$SEND_SNOWFLAKE_NOTIFICATION(
--   SNOWFLAKE.NOTIFICATION.TEXT_PLAIN('OEE Command Center: Slack notifications active!'),
--   SNOWFLAKE.NOTIFICATION.INTEGRATION('SLACK_WEBHOOK_INT')
-- );
