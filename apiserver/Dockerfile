FROM python:3.8-alpine
WORKDIR /code
RUN pip install pipenv==2018.11.26
COPY Pipfile* ./
RUN pipenv install --deploy --system
COPY . .
CMD ["python", "app.py"]
