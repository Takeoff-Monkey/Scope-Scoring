# AWS Lambda Container Image for ERW Job Scorer
FROM public.ecr.aws/lambda/python:3.11

# Install system dependencies for pandas and reportlab
RUN dnf install -y \
    gcc \
    python3-devel \
    && dnf clean all

# Copy requirements file
COPY requirements-lambda.txt ${LAMBDA_TASK_ROOT}/

# Install Python dependencies
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements-lambda.txt

# Copy the Lambda handler
COPY lambda_handler.py ${LAMBDA_TASK_ROOT}/

# Set the CMD to the Lambda handler
CMD [ "lambda_handler.lambda_handler" ]
