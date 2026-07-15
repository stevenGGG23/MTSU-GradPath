# MTSU GradPath

**MTSUGradPath** is a Python application designed to help Middle Tennessee State University (MTSU) students create an optimized path to graduation. The application analyzes degree requirements, prerequisite chains, course offerings, and university policies to generate a personalized semester-by-semester academic plan. Students can also track their progress toward completing their degree and adjust their schedules as they complete coursework.

This project is being developed as a group project for a Computer Science course at Middle Tennessee State University.

---

## Features

- Generate a personalized semester-by-semester path to graduation
- Analyze course prerequisite chains and detect conflicts
- Track completed and remaining degree requirements
- Optimize semester credit loads
- Graphical user interface built with tkinter
- Visual dashboards powered by matplotlib:
  - Credit-hour load per semester (bar chart)
  - Degree progress toward graduation (progress chart)
  - Prerequisite chain view
- SQLite database for course catalog, prerequisites, and saved plans
- Export generated plans to JSON

---

## Technologies Used

- **Python 3.10+** — core application language
- **SQLite (sqlite3)** — storage for the course catalog, degree requirements, and student plans
- **tkinter** — GUI framework (windows, dropdowns, listboxes, tabbed views)
- **CustomTkinter** — modern themed widgets layered on tkinter
- **matplotlib** — charts embedded in the GUI via the TkAgg backend
- **JSON** — catalog seed data and plan export format
- **unittest** — automated testing of the prerequisite resolver and plan generator
- **Git & GitHub** — version control and collaboration

---

## Project Structure

```
MTSUGradPath/
│
├── main.py                  # Application entry point
├── gui.py                   # tkinter/CustomTkinter interface
├── charts.py                # matplotlib visualizations (TkAgg embedding)
├── database.py              # SQLite connection, schema, and queries
├── student.py               # Student model (major, completed courses, progress)
├── course.py                # Course model (credits, prerequisites, offerings)
├── planner.py               # Degree requirement engine
├── scheduler.py             # Semester plan generator and validator
├── utils.py                 # Shared helpers
├── data/
│   ├── courses.json             # Catalog seed data
│   ├── degree_requirements.json # Degree program seed data
│   └── gradpath.db              # SQLite database (generated on first run)
├── tests/
│   ├── test_planner.py
│   └── test_scheduler.py
├── requirements.txt
└── README.md
```

---

## Installation

1. Clone the repository:
```bash
git clone https://github.com/yourusername/MTSUGradPath.git
```

2. Navigate to the project directory:
```bash
cd MTSUGradPath
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Run the application:
```bash
python main.py
```

On first run, the application seeds the SQLite database (`data/gradpath.db`) from the JSON catalog files.

---

## How It Works

1. Select your major and enter completed coursework through the GUI.
2. The application analyzes:
   - Degree requirements
   - Course prerequisite chains
   - University policies
   - Recommended credit loads
3. A semester-by-semester graduation path is generated and displayed, along with charts showing credit load per semester and overall degree progress.
4. As courses are completed, students update their progress and generate an updated academic path. Plans and progress are saved to the SQLite database and can be exported to JSON.

---

## Future Enhancements

- Support for multiple majors and minors
- GPA tracking
- Export schedules to PDF
- Integration with live MTSU course catalog data
- Elective recommendations
- Interactive prerequisite graph visualization

---

## Team Members

- Steven Gobran
- Beshoy Azrak
- Caleb Lykens
- Mina Youssef Eshak

---

## License

This project is intended for educational purposes as part of a university Computer Science course.
