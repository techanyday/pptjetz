# PPTJetX: AI-Powered Google Slides Generator

PPTJetX is a modern web application that leverages Hugging Face’s Mistral-7B-Instruct model to automatically generate compelling slide content, then builds a complete presentation through the Google Slides API.  Generate a beautifully-designed deck in seconds – ready to share or download – without touching PowerPoint.

## 🌟 Features

- **AI-Powered Content Generation**: Hugging Face Mistral-7B-Instruct writes concise, engaging slide text from your prompt
- **Minimalist, Responsive Themes**: Clean design optimised for readability and Google Slides
- **Customization Options**:
  - Number of slides
  - Presenter information
  - Optional image integration
- **Responsive Web Interface**: Modern UI with real-time template preview
- **Instant Google Slides Export**: Receive a shareable Google Slides URL and optional PPTX download link

## 🚀 Getting Started

### Prerequisites

- Python 3.9+
- A free Hugging Face Inference API token (`HF_API_TOKEN`)
- A Google Cloud service-account JSON key with Slides & Drive scope (`GOOGLE_APPLICATION_CREDENTIALS`)

### Installation

1. Clone the repository:
```bash
git clone https://github.com/techanyday/pptjet.git
cd pptjet
```

2. Create and activate a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Create a `.env` file with the following keys (example):
```dotenv
HF_API_TOKEN=hf_xxxxxxxxxxxxxxxxxxxxxxxxxxxxx
GOOGLE_APPLICATION_CREDENTIALS=/absolute/path/your-service-account.json
FLASK_SECRET_KEY=super-secret-key
PAYSTACK_SECRET_KEY=pk_test_xxx    # Only required for pay-per-use payments
```

5. Run the application locally:
```bash
python -m flask --app run.py run --debug
```

Visit `http://127.0.0.1:5000` in your browser to start using PPTJetX.

## 🎯 Usage

1. Fill in the presentation details:
   - Title
   - Presenter name
   - Number of slides
   - Content prompt

2. Select a template style from the visual preview options

3. Click "Generate Presentation"

4. Download your generated .pptx file

## 🛠️ Technical Stack

- **Backend**: Flask (Python)
- **AI Integration**: ChatGPT 3.5 Turbo (via `CHATGPT_API_TOKEN`)
- **Slides Generation**: Google Slides REST API & Drive export
- **Auth & Payments**: Google OAuth 2.0, Paystack (optional)
- **Frontend**: HTML / TailwindCSS / Alpine.js

## 📁 Project Structure

```
PPTJet/
├── app/
│   ├── static/
│   ├── templates/                  # HTML templates
│   └── utils/
│       ├── chatgpt_client.py       # ChatGPT 3.5 Turbo client
│       └── slides_api.py           # Google Slides helpers (batchUpdate)
├── generated/                      # Output directory
├── .env                            # Environment variables
├── requirements.txt                # Python dependencies
└── run.py                          # Application entry point
```

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## 📝 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 🙏 Acknowledgments

- Hugging Face for open access to powerful language models
- Google for the Slides & Drive APIs
- Flask team for the excellent web framework
