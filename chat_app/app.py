"""Chat with your Impala data. Runs as a Cloudera AI Application with authentication ON.

CAI's proxy tells the app who is logged in (Remote-User header, which it sets on every request and
which the browser cannot forge). The user is read per browser session, never stored app-wide, so two
people using the same running app each query as themselves.
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

try:
    chat_model = model()
except Exception as e:
    st.error(f"Model is not configured: {e}")
    st.stop()

st.title("Iceberg data chat")
st.caption(
    f"Signed in as **{user}**. Queries run as you, so you see only the data you are allowed to see. "
    f"Model: {chat_model.label()}"
)

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
            answer = agent.run_turn(chat_model, user, st.session_state.history, on_tool_call=show)
            steps.update(label="Done", state="complete")
        except Exception as e:
            steps.update(label="Failed", state="error")
            answer = f"Something went wrong: {e}"
        st.markdown(answer)
    st.session_state.history.append({"role": "assistant", "content": answer})
