"""Chat with your Impala data. Runs as a Cloudera AI Application with authentication ON.

CAI's proxy tells the app who is logged in (Remote-User header, which it sets on every request and
which the browser cannot forge). The user is read per browser session, never stored app-wide, so two
people using the same running app each query as themselves.

CHAT_TEST_ACT_AS (testing only, off by default): comma-separated CAI users who get an "Act as" box
to query as someone else, e.g. to check the audit log before real testers are available. The audit
log then shows the chosen user, not who was actually typing, so leave this unset outside testing.
"""

import json
import os
import sys

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent  # noqa: E402
import llm  # noqa: E402

st.set_page_config(page_title="Iceberg data chat", page_icon="💬")


def current_user() -> str | None:
    return (st.context.headers.get("Remote-User") or "").strip().lower() or None


@st.cache_resource
def model():
    return llm.from_env()


user = current_user()
if not user:
    st.error(
        "No logged-in CAI user on this request. The chat app must run with CAI authentication turned on "
        "(Application settings: do not allow unauthenticated access)."
    )
    st.stop()

testers = [u.strip().lower() for u in os.getenv("CHAT_TEST_ACT_AS", "").split(",") if u.strip()]
query_user = user
if user in testers:
    with st.sidebar:
        st.warning("Testing only: queries run as the user below, and the audit log will show that user.")
        query_user = st.text_input("Act as", value=user).strip().lower() or user
    if st.session_state.get("query_user") != query_user:
        st.session_state.query_user = query_user
        st.session_state.history = []  # don't carry one user's answers into another's conversation

try:
    chat_model = model()
except Exception as e:
    st.error(f"Model is not configured: {e}")
    st.stop()

st.title("Iceberg data chat")
if query_user == user:
    st.caption(
        f"Signed in as **{user}**. Queries run as you, so you see only the data you are allowed to see. "
        f"Model: {chat_model.label()}"
    )
else:
    st.warning(f"Signed in as **{user}**, ACTING AS **{query_user}** (testing). Model: {chat_model.label()}")

if "history" not in st.session_state:
    st.session_state.history = []

for m in st.session_state.history:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])

if question := st.chat_input("Ask about your data"):
    st.session_state.history.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        steps = st.status("Working…", expanded=False)

        def show(name, args, output):
            steps.write(f"**{name}** `{json.dumps(args)}`")
            steps.code(output[:2000])

        try:
            answer = agent.run_turn(chat_model, query_user, st.session_state.history, on_tool_call=show)
            steps.update(label="Done", state="complete")
        except Exception as e:
            steps.update(label="Failed", state="error")
            answer = f"Something went wrong: {e}"
        st.markdown(answer)
    st.session_state.history.append({"role": "assistant", "content": answer})
