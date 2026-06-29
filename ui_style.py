import streamlit as st


def inject_global_css():
    st.markdown(
        """
        <style>
        :root {
            --bg-main: #07111F;
            --bg-deep: #030712;
            --bg-card: rgba(15, 23, 42, 0.72);
            --bg-card-solid: #0F172A;
            --bg-soft: #111827;
            --border-soft: rgba(148, 163, 184, 0.16);
            --border-blue: rgba(56, 189, 248, 0.32);
            --text-main: #F8FAFC;
            --text-muted: #CBD5E1;
            --text-soft: #94A3B8;
            --blue-main: #38BDF8;
            --blue-soft: #93C5FD;
            --blue-deep: #1D4ED8;
            --shadow-soft: rgba(0, 0, 0, 0.28);
        }

        .stApp {
            background:
                radial-gradient(circle at top left, rgba(56, 189, 248, 0.14), transparent 34%),
                radial-gradient(circle at top right, rgba(147, 197, 253, 0.10), transparent 28%),
                linear-gradient(180deg, #07111F 0%, #030712 100%);
            color: var(--text-main);
        }

        [data-testid="stSidebar"] {
            background:
                linear-gradient(180deg, rgba(15, 23, 42, 0.96) 0%, rgba(3, 7, 18, 0.98) 100%);
            border-right: 1px solid var(--border-soft);
        }

        [data-testid="stSidebar"] * {
            color: var(--text-main);
        }

        .block-container {
            padding-top: 2.5rem;
            padding-bottom: 4rem;
            max-width: 1500px;
        }

        h1 {
            letter-spacing: -0.045em;
            font-weight: 800;
            color: #F8FAFC;
        }

        h2, h3 {
            letter-spacing: -0.035em;
            color: #F8FAFC;
        }

        p, span, label {
            color: var(--text-muted);
        }

        a {
            color: var(--blue-soft) !important;
            text-decoration: none;
        }

        a:hover {
            color: var(--blue-main) !important;
            text-decoration: underline;
        }

        div[data-testid="stMetric"] {
            background:
                linear-gradient(145deg, rgba(15, 23, 42, 0.88), rgba(2, 6, 23, 0.72));
            border: 1px solid var(--border-soft);
            border-radius: 18px;
            padding: 18px 20px;
            box-shadow: 0 16px 36px var(--shadow-soft);
        }

        div[data-testid="stMetric"] label {
            color: var(--text-soft) !important;
            font-weight: 600;
        }

        div[data-testid="stMetricValue"] {
            color: #F8FAFC !important;
        }

        .stButton > button {
            border-radius: 14px;
            border: 1px solid var(--border-blue);
            background:
                linear-gradient(135deg, rgba(56, 189, 248, 0.92), rgba(37, 99, 235, 0.92));
            color: white;
            font-weight: 750;
            transition: all 0.16s ease-in-out;
            box-shadow: 0 10px 24px rgba(37, 99, 235, 0.18);
        }

        .stButton > button:hover {
            transform: translateY(-1px);
            border-color: rgba(147, 197, 253, 0.75);
            box-shadow: 0 16px 32px rgba(56, 189, 248, 0.24);
        }

        .stButton > button:active {
            transform: translateY(0px);
            box-shadow: 0 6px 16px rgba(56, 189, 248, 0.18);
        }

        div[data-testid="stDataFrame"] {
            border-radius: 16px;
            overflow: hidden;
            border: 1px solid var(--border-soft);
            box-shadow: 0 16px 36px rgba(0, 0, 0, 0.20);
        }

        div[data-testid="stExpander"] {
            border-radius: 16px;
            border: 1px solid var(--border-soft);
            background:
                linear-gradient(145deg, rgba(15, 23, 42, 0.62), rgba(2, 6, 23, 0.44));
        }

        div[data-testid="stAlert"] {
            border-radius: 16px;
            border: 1px solid rgba(148, 163, 184, 0.16);
        }

        div[data-baseweb="select"] > div {
            background-color: rgba(15, 23, 42, 0.88);
            border-color: var(--border-soft);
            border-radius: 14px;
        }

        div[data-baseweb="input"] > div {
            background-color: rgba(15, 23, 42, 0.88);
            border-color: var(--border-soft);
            border-radius: 14px;
        }

        textarea {
            background-color: rgba(15, 23, 42, 0.88) !important;
            border-color: var(--border-soft) !important;
            border-radius: 14px !important;
        }

        .stSlider [data-baseweb="slider"] {
            padding-top: 0.7rem;
            padding-bottom: 0.7rem;
        }

        .stTabs [data-baseweb="tab-list"] {
            gap: 8px;
        }

        .stTabs [data-baseweb="tab"] {
            border-radius: 999px;
            background: rgba(15, 23, 42, 0.72);
            border: 1px solid var(--border-soft);
            padding: 8px 16px;
        }

        .stTabs [aria-selected="true"] {
            background:
                linear-gradient(135deg, rgba(56, 189, 248, 0.22), rgba(147, 197, 253, 0.12));
            border: 1px solid var(--border-blue);
        }

        hr {
            border-color: rgba(148, 163, 184, 0.16);
        }

        code {
            color: #BAE6FD;
            background: rgba(15, 23, 42, 0.88);
            border-radius: 8px;
            padding: 2px 6px;
        }

        .calm-card {
            background:
                linear-gradient(145deg, rgba(15, 23, 42, 0.78), rgba(2, 6, 23, 0.64));
            border: 1px solid var(--border-soft);
            border-radius: 18px;
            padding: 18px 20px;
            box-shadow: 0 16px 36px rgba(0, 0, 0, 0.20);
        }

        .soft-caption {
            color: var(--text-soft);
            font-size: 0.92rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
