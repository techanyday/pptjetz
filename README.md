# PPTJet: AI-Powered PowerPoint Generator

PPTJet is a modern web application that leverages OpenAI's GPT models to automatically generate professional PowerPoint presentations. With a selection of beautiful templates and AI-driven content generation, create engaging presentations in minutes.

## 🌟 Features

- **AI-Powered Content Generation**: Automatically generates slide content based on your prompt
- **Multiple Template Styles**:
  - 🎨 Aesthetic: Modern and creative design
  - 👔 Professional: Business-ready presentation
  - 🕰️ Vintage: Classic and elegant style
- **Customization Options**:
  - Number of slides
  - Presenter information
  - Optional image integration
- **Responsive Web Interface**: Modern UI with real-time template preview
- **Download Ready**: Get your presentation in .pptx format instantly

## 🚀 Getting Started

### Prerequisites

- Python 3.8+
- OpenAI API key
- PowerPoint (for template customization)

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

4. Create a .env file with your API keys:
```
OPENAI_API_KEY=your_openai_api_key
FLASK_SECRET_KEY=your_secret_key
PEXELS_API_KEY=your_pexels_api_key  # Optional, for image support
```

5. Run the application:
```bash
python -m flask --app run.py run --debug
```

Visit `http://127.0.0.1:5000` in your browser to start using PPTJet.

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
- **Frontend**: HTML, JavaScript, TailwindCSS
- **PowerPoint Generation**: python-pptx
- **AI Integration**: OpenAI API
- **Image Support**: Pexels API (optional)

## 📁 Project Structure

```
PPTJet/
├── app/
│   ├── static/
│   │   ├── img/                    # Template previews
│   │   └── presentations/
│   │       └── custom_styles/      # Template files
│   ├── templates/                  # HTML templates
│   └── utils/
│       └── ppt_generator.py        # PowerPoint generation logic
├── generated/                      # Output directory
├── .env                           # Environment variables
├── requirements.txt               # Python dependencies
└── run.py                        # Application entry point
```

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## 📝 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 🙏 Acknowledgments

- OpenAI for their powerful GPT models
- python-pptx for PowerPoint generation capabilities
- Flask team for the excellent web framework
- Pexels for image integration support
