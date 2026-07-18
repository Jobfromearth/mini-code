# Optional: closer-to-real local emulation via the actual Lambda base image
# and its built-in Runtime Interface Emulator (RIE). No AWS account needed --
# `public.ecr.aws/lambda/python` is a public, unauthenticated pull, and RIE
# only listens on localhost. See README.md for `local_invoke.py`, which needs
# neither Docker nor this image.
FROM public.ecr.aws/lambda/python:3.12

COPY minicode ${LAMBDA_TASK_ROOT}/minicode
COPY requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt
COPY deploy/lambda/handler.py ${LAMBDA_TASK_ROOT}/

CMD ["handler.lambda_handler"]
