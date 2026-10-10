"""
RideWise Assist — Streamlit Application

Launch with:   streamlit run app.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import streamlit as st

# Ensure src/ is importable regardless of working directory
APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))

from config.settings import DB_PATH
from src.ridewise.auth.session import SessionRole, SessionState
from src.ridewise.database.db import get_connection
from src.ridewise.workflow.router import process_message

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="RideWise Assist",
    page_icon="🛵",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

html, body, [class*="css"] {
    font-family: 'Inter', sans-serif;
}

/* Sidebar */
[data-testid="stSidebar"] {
    background: linear-gradient(160deg, #0f172a 0%, #1e293b 100%);
    border-right: 1px solid #334155;
}
[data-testid="stSidebar"] * { color: #e2e8f0 !important; }
[data-testid="stSidebar"] .stSelectbox label,
[data-testid="stSidebar"] .stTextInput label { color: #94a3b8 !important; font-size: 0.8rem; }

/* Main area */
[data-testid="stAppViewContainer"] {
    background: #0f172a;
}
[data-testid="stMain"] {
    background: transparent;
}

/* Header */
.rw-header {
    display: flex;
    align-items: center;
    gap: 1rem;
    padding: 1.2rem 1.5rem;
    background: linear-gradient(90deg, #1e40af 0%, #7c3aed 100%);
    border-radius: 12px;
    margin-bottom: 1.5rem;
    box-shadow: 0 4px 24px rgba(124, 58, 237, 0.3);
}
.rw-logo { font-size: 2.4rem; }
.rw-title { font-size: 1.6rem; font-weight: 700; color: white; margin: 0; }
.rw-subtitle { font-size: 0.85rem; color: #bfdbfe; margin: 0; }

/* Chat bubbles */
.msg-user {
    background: linear-gradient(135deg, #1e40af, #2563eb);
    color: white;
    padding: 0.9rem 1.2rem;
    border-radius: 16px 16px 4px 16px;
    margin: 0.5rem 0 0.5rem 20%;
    box-shadow: 0 2px 8px rgba(37,99,235,0.3);
    line-height: 1.6;
}
.msg-assistant {
    background: #1e293b;
    color: #e2e8f0;
    padding: 0.9rem 1.2rem;
    border-radius: 16px 16px 16px 4px;
    margin: 0.5rem 20% 0.5rem 0;
    border: 1px solid #334155;
    box-shadow: 0 2px 8px rgba(0,0,0,0.2);
    line-height: 1.6;
}
.msg-system {
    background: #0f2332;
    color: #64748b;
    font-size: 0.8rem;
    padding: 0.5rem 1rem;
    border-radius: 8px;
    text-align: center;
    margin: 0.3rem 10%;
    border: 1px dashed #334155;
}

/* Status badge */
.badge {
    display: inline-block;
    padding: 0.2rem 0.6rem;
    border-radius: 20px;
    font-size: 0.75rem;
    font-weight: 600;
}
.badge-gold { background: #854d0e; color: #fef08a; }
.badge-silver { background: #374151; color: #d1d5db; }
.badge-standard { background: #1e3a5f; color: #93c5fd; }
.badge-agent { background: #14532d; color: #86efac; }

/* Input area */
.stTextInput input {
    background: #1e293b !important;
    color: #e2e8f0 !important;
    border: 1px solid #334155 !important;
    border-radius: 10px !important;
}
.stButton button {
    border-radius: 8px !important;
    font-weight: 600 !important;
}

/* Divider */
hr { border-color: #334155; }

/* Markdown in chat */
.msg-assistant p { margin: 0.4rem 0; }
.msg-assistant ul { margin: 0.4rem 0 0.4rem 1rem; }
.msg-assistant strong { color: #93c5fd; }
</style>
""", unsafe_allow_html=True)

# ── Session state initialisation ──────────────────────────────────────────────

def _init_session():
    if "rw_session" not in st.session_state:
        st.session_state.rw_session = SessionState()
    if "retrieval_ready" not in st.session_state:
        st.session_state.retrieval_ready = False


_init_session()


# ── Retrieval system startup ──────────────────────────────────────────────────

@st.cache_resource(show_spinner=False)
def _load_retrieval():
    """Load retrieval system once at startup (cached across reruns)."""
    try:
        from src.ridewise.retrieval.hybrid import load_retrieval_system
        load_retrieval_system()
        return True, ""
    except FileNotFoundError as exc:
        return False, str(exc)
    except Exception as exc:
        return False, str(exc)


# ── Customer login helper ─────────────────────────────────────────────────────

def _verify_customer(customer_id: str) -> dict | None:
    """
    Look up a customer by ID in the database.
    Returns the customer record dict, or None if not found.
    """
    try:
        con = get_connection()
        cur = con.cursor()
        cur.execute(
            "SELECT customer_id, full_name, customer_tier, kyc_status "
            "FROM customers WHERE customer_id = ?",
            (customer_id.strip().upper(),),
        )
        row = cur.fetchone()
        con.close()
        return dict(row) if row else None
    except Exception:
        return None


# ── Sidebar ───────────────────────────────────────────────────────────────────

def render_sidebar():
    session: SessionState = st.session_state.rw_session

    with st.sidebar:
        st.markdown("### 🛵 RideWise Assist")
        st.markdown("---")

        # Session type selector
        role_choice = st.selectbox(
            "Session type",
            ["— Select —", "Customer", "Support Agent"],
            key="role_selector",
        )

        # ── Customer login ─────────────────────────────────────────────────────
        if role_choice == "Customer":
            st.markdown("#### Customer Login")
            cid_input = st.text_input(
                "Customer ID", placeholder="e.g. CUST0001", key="cid_input"
            )
            if st.button("🔑 Log in", key="login_btn"):
                record = _verify_customer(cid_input)
                if record:
                    session.role = SessionRole.CUSTOMER
                    session.customer_id = record["customer_id"]
                    session.customer_name = record["full_name"]
                    session.customer_tier = record["customer_tier"]
                    session.clear_history()
                    st.success(f"Welcome, {record['full_name']}!")
                    time.sleep(0.5)
                    st.rerun()
                else:
                    st.error("Customer ID not found. Please check and try again.")

        # ── Agent login ────────────────────────────────────────────────────────
        elif role_choice == "Support Agent":
            agent_name = st.text_input("Agent name / ID", placeholder="e.g. Agent-01", key="agent_input")
            if st.button("🔑 Start agent session", key="agent_btn"):
                session.role = SessionRole.AGENT
                session.agent_id = agent_name.strip() or "Agent"
                session.clear_history()
                st.success(f"Agent session started — {session.agent_id}")
                time.sleep(0.5)
                st.rerun()

        # ── Session info ───────────────────────────────────────────────────────
        if session.is_authenticated:
            st.markdown("---")
            if session.is_customer:
                tier = session.customer_tier or "STANDARD"
                tier_class = tier.lower()
                st.markdown(
                    f"**Logged in as:** {session.customer_name}  \n"
                    f"**ID:** `{session.customer_id}`  \n"
                    f"**Tier:** <span class='badge badge-{tier_class}'>{tier}</span>",
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    f"**Agent session**  \n"
                    f"<span class='badge badge-agent'>{session.agent_id}</span>",
                    unsafe_allow_html=True,
                )

            st.markdown("")
            if st.button("🔄 New conversation", key="new_conv"):
                session.clear_history()
                st.rerun()

            if st.button("🚪 Log out", key="logout_btn"):
                st.session_state.rw_session = SessionState()
                st.rerun()

        # ── Retrieval status ───────────────────────────────────────────────────
        st.markdown("---")
        st.markdown("#### System Status")
        retrieval_ok, retrieval_err = _load_retrieval()
        if retrieval_ok:
            st.success("📚 Policy index: ready")
        else:
            st.warning(f"📚 Policy index: not ready\n\n`{retrieval_err[:80]}`")
            st.info("Run `python scripts/ingest_documents.py` to build the index.")

        if DB_PATH.exists():
            st.success("🗄️ Database: connected")
        else:
            st.error("🗄️ Database: not found")


# ── Main chat area ────────────────────────────────────────────────────────────

def render_chat():
    session: SessionState = st.session_state.rw_session

    # Header
    st.markdown("""
    <div class="rw-header">
        <div class="rw-logo">🛵</div>
        <div>
            <p class="rw-title">RideWise Assist</p>
            <p class="rw-subtitle">AI-powered support for RideWise Mobility customers and agents</p>
        </div>
    </div>
    """, unsafe_allow_html=True)

    if not session.is_authenticated:
        st.markdown("""
        <div class="msg-system">
        👈 Please select a session type and log in from the sidebar to begin.
        </div>
        """, unsafe_allow_html=True)

        # Show quick info cards
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("""
            **👤 Customer session**
            - View your bookings, payments and damage claims
            - Get policy guidance on cancellations, late returns and EV fees
            - Calculate expected refunds and charges
            - Ask about RideWise policies
            """)
        with col2:
            st.markdown("""
            **🧑‍💼 Support Agent session**
            - Access operational records across customers
            - Look up specific booking, vehicle or customer details
            - Apply policy rules to support cases
            - Cross-reference payments, damage reports and booking history
            """)
        return

    # ── Conversation history ──────────────────────────────────────────────────
    chat_container = st.container()
    with chat_container:
        if not session.history:
            role_label = (
                f"customer {session.customer_name}" if session.is_customer
                else f"agent {session.agent_id}"
            )
            st.markdown(
                f'<div class="msg-system">New conversation started as {role_label}</div>',
                unsafe_allow_html=True,
            )

        for msg in session.history:
            if msg["role"] == "user":
                st.markdown(
                    f'<div class="msg-user">{msg["content"]}</div>',
                    unsafe_allow_html=True,
                )
            else:
                # Render assistant markdown inside styled div
                import re as _re
                # Basic safety: strip any HTML tags from LLM output before rendering
                safe_content = _re.sub(r"<(?!br|p|ul|li|ol|strong|em|code|pre)[^>]*>", "", msg["content"])
                st.markdown(
                    f'<div class="msg-assistant">{safe_content}</div>',
                    unsafe_allow_html=True,
                )

    # ── Input area ────────────────────────────────────────────────────────────
    st.markdown("---")
    col_input, col_send = st.columns([6, 1])

    with col_input:
        user_input = st.text_input(
            "Message",
            placeholder="Ask about your bookings, payments, policies…",
            label_visibility="collapsed",
            key="user_input",
        )
    with col_send:
        send_clicked = st.button("Send ➤", key="send_btn", use_container_width=True)

    # ── Handle send ───────────────────────────────────────────────────────────
    if send_clicked and user_input.strip():
        msg_text = user_input.strip()
        session.add_message("user", msg_text)

        with st.spinner("RideWise Assist is thinking…"):
            try:
                response = process_message(msg_text, session)
            except Exception as exc:
                response = (
                    f"⚠️ An unexpected error occurred: {str(exc)[:200]}. "
                    "Please try again or contact support."
                )

        session.add_message("assistant", response)
        st.rerun()

    # ── Suggested questions ────────────────────────────────────────────────────
    if session.is_authenticated and not session.history:
        st.markdown("**Quick questions to try:**")
        if session.is_customer:
            suggestions = [
                "Show me my recent bookings",
                "What is the cancellation refund policy?",
                "What is the late return fee for a GOLD tier customer?",
                "Do I have any pending damage claims?",
            ]
        else:
            suggestions = [
                "Show me bookings for CUST0001",
                "What are the late return fees by vehicle model?",
                "Explain the difference between policy v1 and v2",
                "What does claim status DISPUTED mean?",
            ]
        cols = st.columns(2)
        for i, s in enumerate(suggestions):
            with cols[i % 2]:
                if st.button(s, key=f"sug_{i}"):
                    session.add_message("user", s)
                    with st.spinner("RideWise Assist is thinking…"):
                        try:
                            response = process_message(s, session)
                        except Exception as exc:
                            response = f"⚠️ Error: {str(exc)[:200]}"
                    session.add_message("assistant", response)
                    st.rerun()


# ── App entry point ───────────────────────────────────────────────────────────

def main():
    # Pre-load retrieval system (cached)
    _load_retrieval()

    render_sidebar()
    render_chat()


if __name__ == "__main__":
    main()
