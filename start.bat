@echo off
cd /d E:\Shelwell
call .venv\Scripts\activate
streamlit run app/main.py
pause