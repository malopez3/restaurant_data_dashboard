"""Restaurant visits dashboard (Streamlit + Aiven MySQL) - single-screen layout."""
import html
import os
import tempfile

import pandas as pd
import plotly.express as px
import streamlit as st
from sqlalchemy import create_engine
from sqlalchemy.engine import URL

st.set_page_config(
    page_title="Restaurant Visits",
    page_icon="🍽️",
    layout="wide",
    initial_sidebar_state="expanded",
)

ALL = "All restaurants (combined)"
FOOD_COLOR = "#1f6f78"
ALCOHOL_COLOR = "#c8872b"
SEQ = ["#1f6f78", "#c8872b", "#7a9e7e", "#8c5e58", "#4b5d67", "#d9b26f", "#9fc5c8"]
CHART_H = 235  # height of every chart in px; lower it if the page still scrolls on your screen
PLOT_CFG = {"displayModeBar": False}

# Tighten Streamlit's default spacing so everything fits on one screen.
st.markdown(
    """
    <style>
    .block-container {padding-top: 1.6rem; padding-bottom: 0.4rem; padding-left: 1.5rem; padding-right: 1.5rem;}
    header[data-testid="stHeader"] {background: transparent; height: 2.2rem;}
    div[data-testid="stVerticalBlock"] {gap: 0.35rem;}
    [data-testid="stMetric"] {padding: 0;}
    [data-testid="stMetricValue"] {font-size: 1.55rem; line-height: 1.2;}
    [data-testid="stMetricLabel"] p {font-size: 0.78rem;}
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------- database
@st.cache_resource
def get_engine():
    cfg = st.secrets["aiven"]
    connect_args = {}
    ca_cert = cfg.get("ca_cert")
    if ca_cert:
        # Verified TLS using the CA certificate from the Aiven console
        path = os.path.join(tempfile.gettempdir(), "aiven_ca.pem")
        with open(path, "w") as f:
            f.write(ca_cert)
        connect_args["ssl_ca"] = path
    else:
        # Encrypted connection without certificate verification
        connect_args["ssl"] = {"fake_flag_to_enable_tls": True}

    url = URL.create(
        "mysql+pymysql",
        username=cfg["user"],
        password=cfg["password"],
        host=cfg["host"],
        port=int(cfg["port"]),
        database=cfg["dbname"],
    )
    return create_engine(url, connect_args=connect_args, pool_pre_ping=True, pool_recycle=1800)


@st.cache_data(ttl=600, show_spinner="Loading visits from Aiven…")
def load_visits() -> pd.DataFrame:
    # Only the columns the dashboard needs (no SSN or other personal data).
    query = """
        SELECT v.VisitID, v.RestaurantID, r.RestaurantName, r.City, r.State,
               v.CustomerID, v.VisitDate, v.VisitTime, v.PartySize, v.WaitTime,
               v.FoodBill, v.TipAmount, v.FoodDiscountPercentage,
               v.orderedAlcohol, v.AlcoholBill,
               m.MealType AS MealTypeName, p.PaymentType AS PaymentTypeName
        FROM RestaurantVisits v
        JOIN Restaurants r  ON r.rID = v.RestaurantID
        JOIN MealType m     ON m.MealTypeID = v.MealType
        JOIN PaymentType p  ON p.PaymentTypeID = v.PaymentMethod
    """
    df = pd.read_sql(query, get_engine())
    df["VisitDate"] = pd.to_datetime(df["VisitDate"])
    # TIME columns arrive as timedelta; keep the hour of day (NaN when no time was recorded)
    df["VisitHour"] = pd.to_timedelta(df["VisitTime"]).dt.components.hours
    for col in ["FoodBill", "TipAmount", "FoodDiscountPercentage", "AlcoholBill"]:
        df[col] = df[col].astype(float)
    df["Month"] = df["VisitDate"].dt.to_period("M").dt.to_timestamp()
    df["Year"] = df["VisitDate"].dt.year
    return df


# ----------------------------------------------------------------- helpers
def money(x: float) -> str:
    return f"${x:,.0f}"


def hour_label(h) -> str:
    return f"{int(h) % 12 or 12}{'AM' if h < 12 else 'PM'}"


def show(fig, title):
    """Apply the shared compact style (title inside the figure) and render."""
    fig.update_layout(
        title=dict(text=title, x=0, xanchor="left", y=0.97, font=dict(size=14)),
        height=CHART_H,
        margin=dict(l=0, r=0, t=34, b=0),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(size=11),
        legend=dict(orientation="h", y=1.0, yanchor="bottom", x=1, xanchor="right",
                    title_text="", font=dict(size=11)),
    )
    st.plotly_chart(fig, use_container_width=True, config=PLOT_CFG)


def hbar(data, x, y, title, color):
    """Horizontal bar chart with value labels and no x axis."""
    fig = px.bar(data, x=x, y=y, orientation="h", text=x, color_discrete_sequence=[color])
    fig.update_yaxes(categoryorder="total ascending", title=None)
    fig.update_xaxes(title=None, visible=False, range=[0, data[x].max() * 1.2])
    fig.update_traces(textposition="outside", cliponaxis=False)
    show(fig, title)


def count_bar(df, col, label, title, color=FOOD_COLOR):
    counts = df[col].value_counts().rename_axis(label).reset_index(name="Visits")
    hbar(counts, "Visits", label, title, color)


# -------------------------------------------------------------------- data
try:
    data = load_visits()
except Exception as e:  # noqa: BLE001
    st.error(
        "Could not load data from the Aiven database. Check the `[aiven]` secrets "
        "(host, port, user, password, dbname) and that the service is running."
    )
    st.exception(e)
    st.stop()

if data.empty:
    st.warning("The RestaurantVisits table has no rows yet.")
    st.stop()

# ----------------------------------------------------------------- sidebar
st.sidebar.header("Filters")

restaurants = (
    data[["RestaurantID", "RestaurantName", "City", "State"]]
    .drop_duplicates()
    .sort_values(["RestaurantName", "RestaurantID"])
)
# Show the restaurant name; add city/state only if two restaurants share a name.
dup = restaurants["RestaurantName"].duplicated(keep=False)
restaurants["Label"] = restaurants["RestaurantName"].where(
    ~dup, restaurants["RestaurantName"] + " (" + restaurants["City"] + ", " + restaurants["State"] + ")"
)
label_to_id = dict(zip(restaurants["Label"], restaurants["RestaurantID"]))

choice = st.sidebar.selectbox("Restaurant", [ALL] + list(label_to_id))

min_d, max_d = data["VisitDate"].min().date(), data["VisitDate"].max().date()
date_range = st.sidebar.date_input("Visit dates", (min_d, max_d), min_value=min_d, max_value=max_d)
net_food = st.sidebar.toggle(
    "Apply food discounts",
    value=False,
    help="Off: food spend is the billed amount. On: food spend is reduced by each visit's discount percentage.",
)
if st.sidebar.button("Refresh data"):
    load_visits.clear()
    st.rerun()

df = data
if isinstance(date_range, tuple) and len(date_range) == 2:
    df = df[(df["VisitDate"].dt.date >= date_range[0]) & (df["VisitDate"].dt.date <= date_range[1])]
is_all = choice == ALL
if not is_all:
    df = df[df["RestaurantID"] == label_to_id[choice]]

df = df.assign(FoodSpend=df["FoodBill"] * (1 - df["FoodDiscountPercentage"]) if net_food else df["FoodBill"])

# ------------------------------------------------------------------ header
if df.empty:
    st.info("No visits match these filters. Widen the date range to see data.")
    st.stop()

if is_all:
    subtitle = f"{df['RestaurantID'].nunique()} locations"
else:
    r = restaurants[restaurants["Label"] == choice].iloc[0]
    subtitle = f"{r['City']}, {r['State']}"
span = f"{df['VisitDate'].min():%b %Y} – {df['VisitDate'].max():%b %Y}"
st.markdown(
    f"<span style='font-size:1.6rem;font-weight:700'>{'All restaurants' if is_all else html.escape(choice)}</span>"
    f"&nbsp;&nbsp;<span style='opacity:.65'>{subtitle} · {span}</span>",
    unsafe_allow_html=True,
)

# --------------------------------------------------------------------- KPIs
food_total, alcohol_total = df["FoodSpend"].sum(), df["AlcoholBill"].sum()
no_customer = int(df["CustomerID"].isna().sum())
k1, k2, k3, k4, k5, k6 = st.columns(6)
k1.metric("Total visits", f"{len(df):,}")
k2.metric(
    "Unique customers (all)" if is_all else "Unique customers",
    f"{df['CustomerID'].nunique():,}",
    help=("Distinct customers across all restaurants. " if is_all else "")
    + (f"{no_customer:,} visits with no customer recorded are not counted." if no_customer else ""),
)
k3.metric("Food spend", money(food_total))
k4.metric("Alcohol spend", money(alcohol_total))
k5.metric("Avg wait (min)", f"{df['WaitTime'].mean():.1f}")
k6.metric("Avg party size", f"{df['PartySize'].mean():.1f}" if df["PartySize"].notna().any() else "n/a")

# ------------------------------------------------------------------ row 1
a, b, c = st.columns([3, 1.3, 2])

with a:  # visits per month (colored by year), yearly totals in the title
    m = df.groupby("Month").size().reset_index(name="Visits")
    yearly = df.groupby("Year").size()
    m["Year"] = m["Month"].dt.year.map(lambda y: f"{y}: {yearly[y]:,}")  # legend doubles as yearly totals
    fig = px.bar(m, x="Month", y="Visits", color="Year", color_discrete_sequence=SEQ)
    fig.update_xaxes(title=None, tickformat="%b %Y")
    fig.update_yaxes(title=None)
    show(fig, "Visits per month (legend: total per year)")

with b:  # food vs alcohol
    spend = pd.DataFrame({"Type": ["Food", "Alcohol"], "Total": [food_total, alcohol_total]})
    fig = px.pie(spend, names="Type", values="Total", hole=0.55, color="Type",
                 color_discrete_map={"Food": FOOD_COLOR, "Alcohol": ALCOHOL_COLOR})
    fig.update_traces(textinfo="label+percent", textposition="inside",
                      hovertemplate="%{label}: $%{value:,.0f}<extra></extra>")
    fig.update_layout(showlegend=False)
    show(fig, f"Food {money(food_total)} vs. alcohol {money(alcohol_total)}")

with c:  # visit time
    timed = df.dropna(subset=["VisitHour"])
    if timed.empty:
        st.info("No visit times recorded for this selection.")
    else:
        hours = list(range(int(timed["VisitHour"].min()), int(timed["VisitHour"].max()) + 1))
        labels = [hour_label(h) for h in hours]
        h = timed.groupby("VisitHour").size().reindex(hours, fill_value=0).rename_axis("VisitHour").reset_index(name="Visits")
        h["Hour"] = h["VisitHour"].map(hour_label)
        fig = px.bar(h, x="Hour", y="Visits", color_discrete_sequence=[FOOD_COLOR],
                     category_orders={"Hour": labels})
        fig.update_xaxes(type="category", title=None)
        fig.update_yaxes(title=None)
        show(fig, "Visits by hour of day")

# ------------------------------------------------------------------ row 2
d1, d2, d3, d4 = st.columns(4)

if is_all:
    per = df.groupby("RestaurantName").agg(
        Visits=("VisitID", "count"), UniqueCustomers=("CustomerID", "nunique")
    ).reset_index()
    with d1:
        hbar(per, "Visits", "RestaurantName", "Total visits per restaurant", FOOD_COLOR)
    with d2:
        hbar(per, "UniqueCustomers", "RestaurantName", "Unique customers per restaurant", ALCOHOL_COLOR)
else:
    sized = df.dropna(subset=["PartySize"]).copy()
    with d1:
        if sized.empty:
            st.info("No party sizes recorded.")
        else:
            sized["Party"] = sized["PartySize"].clip(upper=10).astype(int).astype(str).replace({"10": "10+"})
            order = [str(i) for i in range(1, 10)] + ["10+"]
            ps = sized.groupby("Party").size().reset_index(name="Visits")
            fig = px.bar(ps, x="Party", y="Visits", text="Visits", color_discrete_sequence=[FOOD_COLOR],
                         category_orders={"Party": order})
            fig.update_xaxes(type="category", title=None)
            fig.update_yaxes(title=None, visible=False)
            show(fig, "Visits by party size")
    with d2:
        fig = px.histogram(df, x="WaitTime", nbins=25, color_discrete_sequence=[ALCOHOL_COLOR])
        fig.add_vline(x=df["WaitTime"].mean(), line_dash="dash", annotation_text="avg")
        fig.update_xaxes(title=None)
        fig.update_yaxes(title=None)
        show(fig, "Wait time (minutes)")

with d3:
    count_bar(df, "PaymentTypeName", "Payment method", "Most common payment methods")
with d4:
    count_bar(df, "MealTypeName", "Meal type", "Most common meal types")
