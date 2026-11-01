FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_ROOT_USER_ACTION=ignore

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY ticker.py webapp.py simulator.py ./
COPY static/ static/

# yfinance keeps its timezone cache under the home directory, so the user needs one
RUN useradd --create-home app
USER app

EXPOSE 5000
# Override the command to run the loader: docker compose run --rm web python ticker.py
CMD ["python", "webapp.py", "--host", "0.0.0.0"]
