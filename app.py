"""Restaurant visits dashboard (Streamlit + Aiven MySQL)."""
import os
import tempfile

import pandas as pd
import plotly.express as px
import streamlit as st
from sqlalchemy import create_engine
from sqlalchemy.engine import URL

st.set_page_config(page_title="Restaurant Visits", page_icon="🍽️", layout="wide")

ALL = "All restaurants (combined)"
FOOD_COLOR = "#1f6f78"
ALCOHOL_COLOR = "#c8872b"
SEQ = ["#1f6f78", "#c8872b", "#7a9e7e", "#8c5e58", "#4b5d67", "#d9b26f", "#9fc5c8"]


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


def hour_label(h: int) -> str:
    return f"{int(h) % 12 or 12} {'AM' if h < 12 else 'PM'}"


def style(fig, height=340):
    fig.update_layout(
        height=height,
        margin=dict(l=0, r=0, t=10, b=0),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        legend_title_text="",
    )
    return fig


def count_chart(df, col, title_label):
    counts = df[col].value_counts().rename_axis(title_label).reset_index(name="Visits")
    fig = px.bar(counts, x="Visits", y=title_label, orientation="h",
                 color_discrete_sequence=[FOOD_COLOR], text="Visits")
    fig.update_yaxes(categoryorder="total ascending", title=None)
    fig.update_xaxes(title=None)
    return style(fig)


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

# -------------------------------------------------------------------- body
st.title("All restaurants" if is_all else choice)
if not is_all:
    r = restaurants[restaurants["Label"] == choice].iloc[0]
    st.caption(f"{r['City']}, {r['State']}")

if df.empty:
    st.info("No visits match these filters. Widen the date range to see data.")
    st.stop()

food_total, alcohol_total = df["FoodSpend"].sum(), df["AlcoholBill"].sum()
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Total visits", f"{len(df):,}")
c2.metric("Unique customers", f"{df['CustomerID'].nunique():,}")
c3.metric("Food spend", money(food_total))
c4.metric("Alcohol spend", money(alcohol_total))
c5.metric("Avg wait (min)", f"{df['WaitTime'].mean():.1f}")

missing = df["CustomerID"].isna().sum()
if missing:
    st.caption(f"{missing:,} visits have no customer recorded and are excluded from unique-customer counts.")

st.divider()

# Visits over time
left, right = st.columns([2, 1])
with left:
    st.subheader("Visits per month")
    split = False
    if is_all:
        split = st.toggle("Split by restaurant", value=False)
    if split:
        m = df.groupby(["Month", "RestaurantName"]).size().reset_index(name="Visits")
        fig = px.bar(m, x="Month", y="Visits", color="RestaurantName", color_discrete_sequence=SEQ)
    else:
        m = df.groupby("Month").size().reset_index(name="Visits")
        fig = px.bar(m, x="Month", y="Visits", color_discrete_sequence=[FOOD_COLOR])
    fig.update_xaxes(title=None)
    st.plotly_chart(style(fig), use_container_width=True)
with right:
    st.subheader("Visits per year")
    y = df.groupby("Year").size().reset_index(name="Visits")
    fig = px.bar(y, x="Year", y="Visits", text="Visits", color_discrete_sequence=[FOOD_COLOR])
    fig.update_xaxes(type="category", title=None)
    st.plotly_chart(style(fig), use_container_width=True)

# Per-restaurant comparison (combined view only)
if is_all:
    st.subheader("By restaurant")
    per = df.groupby("RestaurantName").agg(
        Visits=("VisitID", "count"), UniqueCustomers=("CustomerID", "nunique")
    ).reset_index()
    a, b = st.columns(2)
    with a:
        st.markdown("**Total visits**")
        fig = px.bar(per, x="Visits", y="RestaurantName", orientation="h", text="Visits",
                     color_discrete_sequence=[FOOD_COLOR])
        fig.update_yaxes(categoryorder="total ascending", title=None)
        fig.update_xaxes(title=None)
        st.plotly_chart(style(fig), use_container_width=True)
    with b:
        st.markdown("**Unique customers**")
        fig = px.bar(per, x="UniqueCustomers", y="RestaurantName", orientation="h", text="UniqueCustomers",
                     color_discrete_sequence=[ALCOHOL_COLOR])
        fig.update_yaxes(categoryorder="total ascending", title=None)
        fig.update_xaxes(title=None)
        st.plotly_chart(style(fig), use_container_width=True)

# Spend
st.subheader("Food vs. alcohol spend")
s1, s2 = st.columns([1, 2])
with s1:
    spend = pd.DataFrame({"Type": ["Food", "Alcohol"], "Total": [food_total, alcohol_total]})
    fig = px.pie(spend, names="Type", values="Total", hole=0.55, color="Type",
                 color_discrete_map={"Food": FOOD_COLOR, "Alcohol": ALCOHOL_COLOR})
    fig.update_traces(textinfo="percent+label")
    fig.update_layout(showlegend=False)
    st.plotly_chart(style(fig), use_container_width=True)
with s2:
    if is_all:
        g = df.groupby("RestaurantName")[["FoodSpend", "AlcoholBill"]].sum().reset_index()
        g = g.melt("RestaurantName", var_name="Type", value_name="Total")
        x = "RestaurantName"
    else:
        g = df.groupby("Month")[["FoodSpend", "AlcoholBill"]].sum().reset_index()
        g = g.melt("Month", var_name="Type", value_name="Total")
        x = "Month"
    g["Type"] = g["Type"].map({"FoodSpend": "Food", "AlcoholBill": "Alcohol"})
    fig = px.bar(g, x=x, y="Total", color="Type", barmode="group",
                 color_discrete_map={"Food": FOOD_COLOR, "Alcohol": ALCOHOL_COLOR})
    fig.update_xaxes(title=None)
    fig.update_yaxes(title="Spend ($)", tickprefix="$")
    st.plotly_chart(style(fig), use_container_width=True)

# Payment & meal type
p1, p2 = st.columns(2)
with p1:
    st.subheader("Most common payment methods")
    st.plotly_chart(count_chart(df, "PaymentTypeName", "Payment method"), use_container_width=True)
with p2:
    st.subheader("Most common meal types")
    st.plotly_chart(count_chart(df, "MealTypeName", "Meal type"), use_container_width=True)

# ------------------------------------------------ timing, party size, waits
st.divider()
st.header("Visit timing, party size and wait time")

timed = df.dropna(subset=["VisitHour"])
no_time = len(df) - len(timed)
if no_time:
    st.caption(f"{no_time:,} visits have no visit time recorded and are left out of the hourly charts.")

t1, t2 = st.columns(2)
with t1:
    st.subheader("Visits by hour of day")
    if timed.empty:
        st.info("No visit times recorded for this selection.")
    else:
        by_meal = st.toggle("Color by meal type", value=False)
        hours = list(range(int(timed["VisitHour"].min()), int(timed["VisitHour"].max()) + 1))
        labels = [hour_label(h) for h in hours]
        if by_meal:
            h = timed.groupby(["VisitHour", "MealTypeName"]).size().reset_index(name="Visits")
            h["Hour"] = h["VisitHour"].map(hour_label)
            fig = px.bar(h, x="Hour", y="Visits", color="MealTypeName", color_discrete_sequence=SEQ,
                         category_orders={"Hour": labels})
        else:
            h = timed.groupby("VisitHour").size().reindex(hours, fill_value=0).reset_index(name="Visits")
            h["Hour"] = h["VisitHour"].map(hour_label)
            fig = px.bar(h, x="Hour", y="Visits", color_discrete_sequence=[FOOD_COLOR],
                         category_orders={"Hour": labels})
        fig.update_xaxes(type="category", title=None)
        st.plotly_chart(style(fig), use_container_width=True)
with t2:
    st.subheader("Average wait by hour of day")
    if timed.empty:
        st.info("No visit times recorded for this selection.")
    else:
        w = timed.groupby("VisitHour")["WaitTime"].mean().reindex(hours).reset_index(name="AvgWait")
        w["Hour"] = w["VisitHour"].map(hour_label)
        fig = px.bar(w, x="Hour", y="AvgWait", color_discrete_sequence=[ALCOHOL_COLOR],
                     category_orders={"Hour": labels})
        fig.update_xaxes(type="category", title=None)
        fig.update_yaxes(title="Minutes")
        st.plotly_chart(style(fig), use_container_width=True)

# Party size and wait time
sized = df.dropna(subset=["PartySize"]).copy()
m1, m2, m3, m4 = st.columns(4)
m1.metric("Avg party size", f"{sized['PartySize'].mean():.1f}" if not sized.empty else "n/a")
m2.metric("Average wait (min)", f"{df['WaitTime'].mean():.1f}")
m3.metric("90th percentile wait (min)", f"{df['WaitTime'].quantile(0.9):.0f}")
m4.metric("Longest wait (min)", f"{df['WaitTime'].max():.0f}")

q1, q2 = st.columns(2)
with q1:
    st.subheader("Party size")
    if sized.empty:
        st.info("No party sizes recorded for this selection.")
    else:
        sized["Party"] = sized["PartySize"].clip(upper=10).astype(int).astype(str).replace({"10": "10+"})
        order = [str(i) for i in range(1, 10)] + ["10+"]
        ps = sized.groupby("Party").size().reset_index(name="Visits")
        fig = px.bar(ps, x="Party", y="Visits", text="Visits", color_discrete_sequence=[FOOD_COLOR],
                     category_orders={"Party": order})
        fig.update_xaxes(type="category", title="Guests in party")
        st.plotly_chart(style(fig), use_container_width=True)
with q2:
    st.subheader("Wait time distribution")
    fig = px.histogram(df, x="WaitTime", nbins=30, color_discrete_sequence=[ALCOHOL_COLOR])
    fig.add_vline(x=df["WaitTime"].mean(), line_dash="dash", annotation_text="average")
    fig.update_xaxes(title="Wait (minutes)")
    fig.update_yaxes(title="Visits")
    st.plotly_chart(style(fig), use_container_width=True)

if is_all:
    st.subheader("Average wait and party size by restaurant")
    comp = df.groupby("RestaurantName").agg(
        AvgWait=("WaitTime", "mean"), AvgParty=("PartySize", "mean")
    ).reset_index()
    c_a, c_b = st.columns(2)
    with c_a:
        fig = px.bar(comp, x="AvgWait", y="RestaurantName", orientation="h",
                     color_discrete_sequence=[ALCOHOL_COLOR])
        fig.update_yaxes(categoryorder="total ascending", title=None)
        fig.update_xaxes(title="Average wait (minutes)")
        st.plotly_chart(style(fig), use_container_width=True)
    with c_b:
        fig = px.bar(comp, x="AvgParty", y="RestaurantName", orientation="h",
                     color_discrete_sequence=[FOOD_COLOR])
        fig.update_yaxes(categoryorder="total ascending", title=None)
        fig.update_xaxes(title="Average party size")
        st.plotly_chart(style(fig), use_container_width=True)

with st.expander("View underlying visits"):
    st.dataframe(
        df.drop(columns=["Month", "Year", "VisitHour"]).sort_values("VisitDate", ascending=False),
        use_container_width=True, hide_index=True,
    )
