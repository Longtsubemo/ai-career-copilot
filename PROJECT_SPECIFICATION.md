# AI Career Copilot

## Project Overview

AI Career Copilot is a full-stack AI-powered job application assistant
designed as a portfolio project. Its purpose is to help discover
relevant AI/ML job opportunities, analyze them using Google Gemini,
tailor resumes, generate cover letters, prepare interview questions, and
track job applications.

The assistant is **not** intended to automatically submit applications.
Instead, it assists with the repetitive parts of the workflow while
keeping the final application decision under the user's control.

------------------------------------------------------------------------

# Goals

-   Discover AI/ML job opportunities.
-   Score each job against a master resume.
-   Generate tailored resumes.
-   Generate personalized cover letters.
-   Prepare interview questions.
-   Track applications.
-   Build a production-quality portfolio project.

------------------------------------------------------------------------

# Technology Stack

## Backend

-   Python 3.12
-   FastAPI
-   SQLAlchemy
-   SQLite
-   Pydantic
-   Playwright

## Frontend

-   React
-   Vite
-   Tailwind CSS
-   Axios

## AI

-   Google Gemini Free API

## Deployment

-   AWS EC2 (future)
-   Docker (optional)

------------------------------------------------------------------------

# Core Features

## 1. Job Collection

Use Playwright to:

-   Login to LinkedIn
-   Search AI Engineer roles
-   Collect:
    -   Job title
    -   Company
    -   Location
    -   Experience
    -   Posted date
    -   Easy Apply availability
    -   Job description
    -   Job URL

Store results in SQLite and avoid duplicates.

------------------------------------------------------------------------

## 2. Database

### Jobs

-   id
-   title
-   company
-   location
-   experience
-   description
-   url
-   easy_apply
-   score
-   status
-   created_at

### Applications

-   id
-   job_id
-   applied_date
-   status
-   notes

------------------------------------------------------------------------

## 3. Gemini Integration

Input:

-   Master Resume
-   Job Description

Return structured JSON:

``` json
{
  "score": 92,
  "should_apply": true,
  "matching_skills": [],
  "missing_skills": [],
  "resume_improvements": [],
  "summary": ""
}
```

------------------------------------------------------------------------

## 4. Resume Tailoring

Gemini should:

-   Rewrite existing bullets
-   Improve ATS optimization
-   Reorder skills
-   Improve professional summary

**Never fabricate experience.**

------------------------------------------------------------------------

## 5. Cover Letter

Generate:

-   Personalized
-   One page maximum
-   Professional tone

------------------------------------------------------------------------

## 6. Interview Preparation

Generate:

-   Technical questions
-   Behavioral questions
-   Suggested answers
-   Topics to revise

------------------------------------------------------------------------

## 7. Dashboard

Features:

-   Job cards
-   Match score
-   Easy Apply indicator
-   Filters
-   Analyze button
-   Generate Resume
-   Generate Cover Letter
-   Open LinkedIn
-   Mark Applied
-   Reject

------------------------------------------------------------------------

## 8. Application Tracker

Track:

-   Applied
-   Interview
-   Rejected
-   Offer
-   Wishlist

Statistics:

-   Total applications
-   Interview rate
-   Average match score

------------------------------------------------------------------------

## 9. Resume Management

Maintain:

-   Master Resume
-   Tailored versions
-   Downloadable PDFs

------------------------------------------------------------------------

## 10. Analytics

Charts for:

-   Applications over time
-   Interview rate
-   Average score
-   Missing skills
-   Top locations
-   Top companies

------------------------------------------------------------------------

# Suggested Project Structure

``` text
ai-career-copilot/

backend/
│
├── api/
├── services/
├── scraper/
├── database/
├── models/
├── schemas/
├── ai/
├── prompts/
└── main.py

frontend/
├── src/
├── components/
├── pages/
├── hooks/
└── services/

README.md
.env.example
docker-compose.yml
```

------------------------------------------------------------------------

# Development Principles

-   Modular architecture
-   Async where appropriate
-   Type hints
-   Environment variables
-   Proper logging
-   Reusable services
-   Production-quality code
-   Well documented

------------------------------------------------------------------------

# Future Roadmap

-   Wellfound integration
-   Naukri integration
-   Indeed integration
-   Email notifications
-   Recruiter CRM
-   Salary estimation
-   Skill-gap analysis
-   Scheduled scraping
-   Multi-resume support

------------------------------------------------------------------------

# Development Strategy

Build incrementally.

1.  Folder structure
2.  Backend
3.  Database models
4.  FastAPI endpoints
5.  Playwright scraper
6.  Gemini client
7.  React frontend
8.  Integration
9.  Testing
10. Documentation

Explain design decisions at each milestone before moving on.

------------------------------------------------------------------------

# Portfolio Deliverables

Include:

-   README.md
-   Architecture diagram
-   Screenshots
-   Unit tests
-   GitHub Actions
-   .env.example
-   Docker Compose
-   Clean commit history

------------------------------------------------------------------------

# Success Criteria

The completed project should demonstrate:

-   AI engineering
-   LLM integration
-   Web automation
-   Backend development
-   Frontend development
-   Database design
-   API development
-   Production-ready software engineering practices

This project should be suitable for showcasing on GitHub and discussing
during AI Engineer interviews.
