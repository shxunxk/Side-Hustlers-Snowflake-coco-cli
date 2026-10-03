"""Snowpark session for Streamlit in Snowflake."""
import streamlit as st
from snowflake.snowpark.context import get_active_session


@st.cache_resource
def get_session():
    return get_active_session()
