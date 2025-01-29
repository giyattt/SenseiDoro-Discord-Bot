# Use an official Python runtime as the base image
FROM python:3.11-slim

# Set the working directory in the container
WORKDIR /app

# Copy the current directory (including requirements.txt) into the container
COPY . /app

# Install dependencies from requirements.txt except py-cord
RUN pip install --no-cache-dir -r requirements.txt

# Install py-cord separately to avoid conflicts with discord.py
RUN pip install --no-cache-dir py-cord==2.4.0

# Expose any necessary ports (if applicable)
# EXPOSE 8080  # Uncomment and change the port if your bot uses a specific port

# Set the environment variables if needed (for example, API keys, config vars, etc.)
# ENV API_KEY=your-api-key

# Define the command to run the bot when the container starts
CMD ["python", "run_sensei.py"]  # Replace 'bot.py' with the entry file for your bot if it's different

# Optionally, you can add this if you want to handle different environments
# ENV PYTHONUNBUFFERED=1