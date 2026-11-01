# Restaurant Visits Dashboard (Streamlit)

## Run locally
    pip install -r requirements.txt
    cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # then fill in values
    streamlit run app.py

## Deploy on Streamlit Community Cloud
1. Push this folder to a GitHub repo (`.gitignore` already excludes secrets).
2. At share.streamlit.io choose **New app**, pick the repo, and set the main file to `app.py`.
3. Under **Advanced settings > Secrets**, paste the `[aiven]` block from `secrets.toml.example`.
4. Deploy.

## Aiven notes
- Use the same values as your R `db_*_aiven` variables (host, port, user, password, db name).
- In the Aiven console, confirm the service's allowed IP addresses permit outside connections
  (the default is open to all; Streamlit Cloud has no fixed IP).
- Data is cached for 10 minutes; use **Refresh data** in the sidebar to reload.
