FROM python:3.8

COPY . /marketdata

WORKDIR /marketdata/app

RUN pip3 install -r ../requirements.txt

ENV PYTHONPATH "${PYTHONPATH}:/marketdata"

EXPOSE 8002

CMD ["python","-m","uvicorn","main:app","--host=0.0.0.0","--reload","--port","8002"]


#  build an image using this command: sudo docker build -t cryptomarketdataimage:0.1 .
#  run the   image using this command: sudo docker run -p 8002:8002 --name marketdata cryptomarketdataimage:0.1

