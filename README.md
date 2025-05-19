# SWE-Bench Bug Report Collector

This project saves previously resolved bug reports along with their corresponding commit history, formatted for use with [SWE-Bench](https://github.com/allenai/swe-bench).

---


## 🚀 How to Run

### 0. Clone the Target Repository

You must clone the GitHub repository (for which you're collecting bug reports) to a local folder named `Repodir`:

```bash
git clone https://github.com/sphinx-doc/sphinx Repodir
````

> Replace the URL with your target repository.

---

### 1. Collect Bug Reports and Linked Commits

Run the following script to gather bug reports with commit links:

```bash
python collect_br_with_commits.py
```

---

### 2. Save Index Files for History Data (Training Data)

Create index files to represent historical training data:

```bash
python save_index.py --repodir Repodir --repo_name repo_name
```

---

### 3. Save Index Files for Test Data

Create index files for test-ready evaluation:

```bash
python save_test_data.py --repodir Repodir --repo_name repo_name
```

---

## 📁 Output

* All intermediate and final results are saved inside the `Repodir` folder.
* This includes:

  * JSON/CSV data files for bug reports.
  * Commit mapping and metadata.
  * Index files for training and testing.

---

## 📝 Requirements

* Python 3.11+
* SWEBench 3.0.15


---

## 🔧 Notes

* The script assumes GitHub repo names in the format: `owner+repo` (e.g., `sphinx-doc+sphinx`).
* Make sure your GitHub API token is available via an environment variable:

```bash
export GITHUB_TOK=your_token_here
```

---

