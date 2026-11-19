# TUPAS (Technological University of the Philippines Academic Study-Buddy)

An interactive, gamified academic reviewer and spaced-repetition study platform built for Filipino college and senior high school students.

---

## 🌟 Key Features

- **⚡ 1-Click AI Reviewer Deck Generator (`/ai/generate/`)**
  - Drop a lecture handout (`.pdf`, `.pptx`, `.txt`, or slide photo) or paste notes.
  - Automatically synthesizes **3D Flip Cards** (with 4 exam choices), **Smart Highlight Notes** (cloze deletion with `[[double brackets]]`), and **Enumeration Drills**.
  - Smart fallback engine: Works 100% locally out-of-the-box even without an external API key!
  - Supports multiple AI providers: Gemini 2.5 Flash, Gemini Flash-Lite, Groq (Llama 3.3 70B), OpenRouter, or TUPAS Smart Offline Engine.
  - Auto-assigns subject to the student's strand/course or General Studies if left blank.

- **🤖 TUPAS Study Buddy (`/ai-generator/<doc_id>/chat/`)**
  - Interactive PDF AI tutor powered by `pypdf` text extraction.
  - Ask questions about your handouts with quick action chips (Summarize, Explain in Simple Taglish, Extract Enumerations, Key Terms, Quiz Me).
  - 1-Click button to convert the uploaded PDF into a full deck.

- **🧠 Active Recall & 6 Exam Modes**
  - Multiple Choice, Identification, Enumeration (ordered/unordered), True/False, Matching, and Mixed Exams.
  - Spaced Repetition engine (`ItemProgress`) with Leitner-inspired intervals.

- **🏆 Gamification & Leaderboard**
  - Daily streaks, study goals (Asia/Manila timezone), achievements & badges.
  - Student Leaderboard filtered by Overall, Course, and School (Staff & Admin accounts strictly hidden).

---

## 🚀 Quick Start (Local Development)

### 1. Requirements
- Python 3.10+
- XAMPP / MySQL or SQLite (default)

### 2. Setup Virtual Environment & Install Dependencies
```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Database Migrations
```bash
python manage.py migrate
```

### 4. Optional AI Provider API Keys
Set any of the following environment variables (PowerShell):
```powershell
$env:GEMINI_API_KEY = "your-gemini-key"
# or
$env:GROQ_API_KEY = "your-groq-key"
# or
$env:OPENROUTER_API_KEY = "your-openrouter-key"
```
*(If no key is configured, TUPAS automatically switches to the built-in local offline engine!)*

### 5. Run the Server
```bash
python manage.py runserver
```
Open [http://127.0.0.1:8000](http://127.0.0.1:8000) in your browser.

---

## 🧪 Running Tests
```bash
python manage.py test
```
