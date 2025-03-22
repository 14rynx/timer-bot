FROM python:3.11

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY src/ .
RUN mkdir -p data

CMD ["python", "-u", "./main.py"]
