FROM public.ecr.aws/lambda/python:3.13

# Install backend dependencies (PyMuPDF needs C libs, included in base image)
COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Copy backend modules
COPY backend/invoice_recon/ ${LAMBDA_TASK_ROOT}/invoice_recon/

# Copy shared Lambda utilities
COPY infra/lambda/shared/ ${LAMBDA_TASK_ROOT}/shared/

# Copy handler
COPY infra/lambda/index_document/handler.py ${LAMBDA_TASK_ROOT}/

CMD ["handler.lambda_handler"]
