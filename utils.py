import os
from datetime import datetime
import requests
from git import Repo
import pickle

# Date cutoff of test data
repo_cutoff = {
    "astropy+astropy": "2018-02-07T15:05:31Z",
    "django+django": "2016-11-08T17:27:19Z",
    "matplotlib+matplotlib": "2019-04-19T01:47:57Z",
    "mwaskom+seaborn": "2022-10-09T23:31:20Z",
    "pallets+flask": "2023-03-04T18:36:21Z",
    "psf+requests": "2013-01-25T05:19:16Z",
    "pydata+xarray": "2019-04-17T21:52:37Z",
    "pylint-dev+pylint": "2021-06-07T15:14:31Z",
    "pytest-dev+pytest": "2019-05-14T21:54:55Z",
    "scikit-learn+scikit-learn": "2017-07-06T11:03:14Z",
    "sphinx-doc+sphinx": "2020-04-08T13:46:43Z",
    "sympy+sympy": "2016-09-15T20:01:58Z"
}


def get_bug_issues_before_date(reponame, label, before_date, auth_token=None):
    owner, repo = reponame.split("+")
    url = f"https://api.github.com/repos/{owner}/{repo}/issues"
    headers = {"Accept": "application/vnd.github+json"}
    #print(url)
    if auth_token:
        headers["Authorization"] = f"token {auth_token}"
    else:
        print("Provide Github Token: create one at https://github.com/settings/tokens")
    
    params = {
        "state": "all",
        "labels": label,
        "per_page": 100,
        "page": 1
    }

    before_dt = datetime.fromisoformat(before_date.replace("Z", "+00:00"))
    bug_issues = []

    while True:
        response = requests.get(url, headers=headers, params=params)
        if response.status_code != 200:
            raise Exception(f"GitHub API error: {response.status_code} {response.text}")
        
        issues = response.json()

        if not issues:
            break

        for issue in issues:
            created_at = datetime.fromisoformat(issue["created_at"].replace("Z", "+00:00"))
            if created_at < before_dt:
                bug_issues.append({
                    "number": issue["number"],
                    "title": issue["title"],
                    "body": issue["body"],
                    "created_at": issue["created_at"],
                    "url": issue["html_url"]
                })

        # Stop if all issues are older than the cutoff date
        last_issue_date = datetime.fromisoformat(issues[-1]["created_at"].replace("Z", "+00:00"))
        if last_issue_date < before_dt:
            break

        params["page"] += 1

    return bug_issues



def get_revised_files(commit_sha, repo_path='.'):
    """
    Returns a list of revised (added or modified) files in a given commit
    using GitPython.
    """
    repo = Repo(repo_path)
    commit = repo.commit(commit_sha)

    revised_files = []

    # Compare this commit to its parent (assumes 1 parent)
    if commit.parents:
        parent = commit.parents[0]
        diffs = commit.diff(parent)

        for diff in diffs:
            if diff.change_type in {'M'}:  # 'A',  , 'D'Added, Modified, Deleted
                revised_files.append(diff.b_path)
    else:
        # This is likely the root commit; all files are considered added
        revised_files = [item.a_path for item in commit.tree.traverse() if item.type == 'blob']

    return revised_files

def save_mapping_brs(Repodir, reponame, auth_token):
    if not os.path.isdir("past_brid2commit"):
        os.makedirs("past_brid2commit")

    repo_path = f"{Repodir}/{reponame}"
    cutoff = repo_cutoff[reponame]
    label = "bug"
    bugs = get_bug_issues_before_date(reponame, label, cutoff, auth_token)
    bugs += get_bug_issues_before_date(reponame, 'Bug', cutoff, auth_token)
    bugs += get_bug_issues_before_date(reponame, 'type:bug', cutoff, auth_token)
    print("Past BRs:", reponame, cutoff, len(bugs), sep='\t') # Past history

    # Get commits
    repo = Repo(repo_path)
    commits = list(repo.iter_commits(repo.head.ref))  
    # Define date range
    dt_upper = datetime.fromisoformat(cutoff.replace("Z", "+00:00"))
    
    # Map commit to BR
    temp_brid2commit = {}
    for bug in bugs:
        cutoff_lower = bug['created_at']
        dt_lower = datetime.fromisoformat(cutoff_lower.replace("Z", "+00:00"))
        brid = bug['number']
        k1, k2 = f"#{brid}",  f"# {brid}"
    
        for commit in commits:
            if commit.committed_datetime < dt_upper and commit.committed_datetime>=dt_lower:
                chash = commit.hexsha
                text = f"{commit.summary}\n{commit.message}"
                if k1 in text or k2 in text:
                    if brid in temp_brid2commit.keys():
                        temp_brid2commit[brid].append(chash)
                    else:
                        temp_brid2commit[brid] = [chash]
    
    brid2commit = {}
    for brid, commit in temp_brid2commit.items():
        commit = set(commit)
        if len(commit)==1:
            brid2commit[brid] = commit.pop()

    # Only collect bugs with commit that Modified python file
    final_bugs = []
    for bug in bugs:
        try:
            commit_sha = brid2commit[bug['number']]
        except:
            continue
        files = get_revised_files(commit_sha, repo_path)
        pyfiles = [x for x in files if x.endswith(".py")]
        if len(pyfiles)>0:
            bug['fixed'] = pyfiles
            final_bugs.append(bug)
        else:
            del brid2commit[bug['number']]

    print("BRs with mapped commits: ", reponame, len(brid2commit)) # BR with mapped commits
    
    with open(f"past_brid2commit/{reponame}_bugs_brid2commit.pkl",'wb') as f:
        pickle.dump((final_bugs, brid2commit),f)
