import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# API Keys
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
NEWS_API_KEY = os.getenv('NEWS_API_KEY')

# Base URLs
OPENF1_BASE_URL = os.getenv('OPENF1_BASE_URL', 'https://api.openf1.org/v1')
JOLPICA_BASE_URL = os.getenv('JOLPICA_BASE_URL', 'http://api.jolpi.ca/ergast/f1')

# Validate required environment variables
if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY not found in environment variables")
if not NEWS_API_KEY:
    raise ValueError("NEWS_API_KEY not found in environment variables")
