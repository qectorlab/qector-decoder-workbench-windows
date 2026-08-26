FROM python:3.10-slim

# Create a non-root user for security
RUN useradd -m -s /bin/bash qector

# Set environment variables
ENV QECTOR_DATA_DIR=/data
ENV PYTHONUNBUFFERED=1
ENV QECTOR_AIRGAP=1

# Create required directories with correct permissions
RUN mkdir -p /data/state /data/logs /app/wheels && \
    chown -R qector:qector /data /app

WORKDIR /app

# Copy the bundled application and wheels
COPY --chown=qector:qector . /app/
# Ensure the wheel is present in the wheels directory.
# Pinned versions (no PyPI fallback): install only from the bundled wheels
# and fail the build if anything is missing, instead of silently pulling
# unpinned packages from the network.
RUN pip install --no-cache-dir --no-index --find-links=/app/wheels \
    qector-decoder-v3==1.0.0 \
    || (echo "ERROR: required wheel missing from /app/wheels" && exit 1)
RUN pip install --no-cache-dir \
    customtkinter==6.0.0 "numpy==2.2.6" scipy==1.18.0 matplotlib==3.11.1 \
    Pillow==12.3.0 psutil==7.2.2 cryptography==49.0.0 reportlab==4.4.9

# Switch to the non-root user
USER qector

# Default command: run the MCP server headlessly
CMD ["python", "main.py", "--mcp"]
