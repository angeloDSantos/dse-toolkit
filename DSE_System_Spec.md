# Delegate Sales Engine (DSE) -- Full System Specification

## Purpose

The **Delegate Sales Engine (DSE)** is an automated workflow designed
to:

1.  Collect relevant executive contact data
2.  Conduct multi-channel outreach
3.  Monitor and interpret responses
4.  Categorise replies
5.  Create opportunities in Salesforce
6.  Send summit information
7.  Schedule onboarding calls

The system integrates the following platforms:

-   Salesforce
-   LinkedIn
-   Outlook
-   SMS (Zoom automation)
-   Browser automation tools

The goal of the system is to **identify, qualify, and onboard summit
delegates efficiently.**

------------------------------------------------------------------------

# 1. System Architecture Overview

The DSE system consists of the following layers:

Data Collection Layer ↓ Data Cleaning & Validation Layer ↓ Outreach
Engine ↓ Reply Monitoring Engine ↓ AI Response Classification ↓
Salesforce Opportunity Creation ↓ Summit Information Email ↓ Calendar
Scheduling ↓ Logging & Database Storage

Each stage must be **modular** so the system can run continuously or in
batches.

------------------------------------------------------------------------

# 2. Data Collection Layer

## Objective

Build a structured prospect database from multiple sources.

### Data Sources

-   Salesforce CRM
-   LinkedIn scraping
-   External enrichment tools
-   Company websites
-   Existing internal databases

------------------------------------------------------------------------

## 2.1 Salesforce Data Extraction

The system pulls contacts using the following filters.

### Filters

-   Job Title: Director / Head / VP / C-Level
-   Region: UK, US, Europe (depending on summit)
-   Relevant industries
-   Past event attendance

### Fields Extracted

First Name\
Last Name\
Company\
Job Title\
Email\
Phone Number\
LinkedIn URL\
Salesforce Contact ID\
Industry\
Region\
Previous Engagement Notes

### Output

`salesforce_contacts.csv`

------------------------------------------------------------------------

## 2.2 LinkedIn Scraping

Contacts are scraped from:

-   LinkedIn search results
-   Sales Navigator lists
-   Company employee lists

### Fields Collected

First Name\
Last Name\
Job Title\
Company\
LinkedIn URL\
Location\
Industry

### Output

`linkedin_contacts.csv`

------------------------------------------------------------------------

## 2.3 Data Enrichment

The system merges Salesforce and LinkedIn data and fills missing fields.

### Enrichment Tasks

-   Email discovery
-   Phone number discovery
-   Company size
-   Industry tags

### Output

`master_outreach_database.csv`

Fields:

First Name\
Last Name\
Company\
Job Title\
Email\
Phone\
LinkedIn URL\
Industry\
Region\
Source\
Salesforce ID

------------------------------------------------------------------------

# 3. Data Cleaning & Filtering

Before outreach begins, the system must validate all contacts.

### Remove contacts if:

-   Record Type = Sponsor
-   Record Type ≠ Delegate
-   Invalid email format
-   Duplicate email
-   Duplicate phone
-   Switchboard phone number
-   Missing first name

### Role prioritisation

1 Head of\
2 VP\
3 Director

### Output

`validated_outreach_list.csv`

------------------------------------------------------------------------

# 4. Outreach Engine

The DSE performs outreach through three channels.

LinkedIn Messages\
Outlook Mail Merge\
SMS (Zoom Automation)

Each channel runs independently but uses the same contact database.

------------------------------------------------------------------------

# 5. LinkedIn Outreach

LinkedIn outreach uses browser automation.

### Process

Open LinkedIn profile\
Send connection request or message\
Insert template message\
Send message\
Log outreach attempt

### Example Message

Hi \[First Name\],

I'm reaching out to invite you to the \[Summit Name\].

Based on your role at \[Company\], I thought this could be a valuable
conversation for you.

Would you be open to hearing a little more?

Best,\
Ben

------------------------------------------------------------------------

# 6. Outlook Email Outreach

Email outreach is executed through **Outlook Mail Merge**.

### Process

Load validated_outreach_list.csv\
Select email template\
Insert personalised fields\
Send emails\
Log email sends

------------------------------------------------------------------------

# 7. SMS Outreach (Zoom Automation)

SMS outreach is executed via the Zoom SMS automation system.

### Process

Load contact database\
Send personalised SMS template\
Log sent SMS

------------------------------------------------------------------------

# 8. Outreach Logging System

Every message sent must be recorded in a **logging database**.

This prevents duplicate outreach and tracks communication history.

### Logging Rules

Whenever the system sends:

LinkedIn message\
Email\
SMS

it must create a log entry.

### Logging Timing

The system should support two modes:

Immediate Logging\
Batch Logging

Immediate logging writes to the database instantly.

Batch logging temporarily stores entries and writes them later if:

-   Logging would interrupt the user's workflow
-   The system is performing high-volume tasks

The user must be able to choose the logging mode.

------------------------------------------------------------------------

## Log Database Fields

Contact Name\
Company\
Email\
Phone\
LinkedIn URL\
Channel\
Message Sent\
Timestamp\
Campaign Name\
Salesforce ID

Example database file:

`outreach_log.db`

------------------------------------------------------------------------

# 9. Reply Monitoring System

The system continuously monitors incoming replies.

### Channels monitored

Outlook inbox\
LinkedIn messages\
SMS inbox

### Polling Interval

Every 5 minutes

### Reply Database

`reply_log.csv`

Fields:

Contact Name\
Channel\
Reply Text\
Timestamp\
Original Campaign\
Salesforce ID

------------------------------------------------------------------------

# 10. AI Response Classification

Every reply must be analysed by AI and categorised.

### Categories

Not Interested\
Potential Interest\
Interested but Unavailable\
Available / Interested

------------------------------------------------------------------------

## Not Interested

Examples:

Not interested\
Please remove me\
No thanks\
Unsubscribe

Action:

Update Salesforce status = Not Interested\
Stop outreach

------------------------------------------------------------------------

## Potential Interest

Examples:

Tell me more\
Send information\
What is the event

Action:

Send Summit Information Email

------------------------------------------------------------------------

## Interested but Unavailable

Examples:

Interested but cannot attend\
Busy on those dates

Action:

Update Salesforce status = Interested but Unavailable

------------------------------------------------------------------------

## Available / Interested

Examples:

Yes I'm interested\
Let's discuss\
Sounds good

Action:

Create Salesforce opportunity\
Send summit information email\
Schedule onboarding call

------------------------------------------------------------------------

# 11. Salesforce Opportunity Creation

When a positive response occurs, the system must create a Salesforce
opportunity.

### Opportunity Fields

Contact\
Company\
Summit Name\
Stage = Interested\
Source = DSE Outreach\
Owner = Ben

Activity log:

Delegate Outreach Response\
Channel\
Reply Text

------------------------------------------------------------------------

# 12. Summit Campaign Configuration

Summits are defined using a configuration object.

Example fields:

Summit Name\
Venue\
City\
Dates\
Audience\
Mission Tracks\
Namedrop Companies\
Flight Contribution\
Hotel Nights\
Attachments

------------------------------------------------------------------------

# 13. Summit Email Generation

The system generates the summit information email dynamically.

Example structure:

Greeting\
Summit introduction\
Purpose of the event\
Namedrop companies\
Mission tracks\
Logistics\
Travel coverage\
Attachments

------------------------------------------------------------------------

# 14. Calendar Scheduling System

Once a prospect shows interest, the system schedules a call.

### Constraints

Duration: 15 minutes\
Time Window: 11:00 -- 18:30 UK Time\
Platform: Zoom

Zoom Link

https://gdssummits.zoom.us/j/2376325263

------------------------------------------------------------------------

## Calendar Invite Email

Subject

Summit Intro -- Ben x \[First Name\]

Body

Hi \[First Name\],

Just putting a placeholder in your calendar to remind you of the
upcoming summit.

If there's any issues with the timing of the onboarding or scheduling
conflicts please let me know.

Zoom link: https://gdssummits.zoom.us/j/2376325263

Thanks, Ben

------------------------------------------------------------------------

# 15. Special Request Detection

AI must detect special requests in replies.

Examples:

Interested in speaking\
Request attendee list\
Interested only if certain companies attend\
Request vendor details

These must be stored in Salesforce.

Field:

Delegate Request Notes

------------------------------------------------------------------------

# 16. Human Review Layer

Before sending summit information emails, the system must allow:

Human review\
Template editing\
Attachment confirmation

This prevents incorrect information being sent.

------------------------------------------------------------------------

# 17. Final DSE Workflow

1 Collect contacts 2 Enrich and merge data 3 Clean and validate contacts
4 Launch outreach campaigns 5 Log all messages 6 Monitor replies 7
Classify responses using AI 8 Create Salesforce opportunities for
positive replies 9 Generate summit information email 10 Allow human
review 11 Send summit email 12 Schedule onboarding call 13 Log special
requests 14 Continue follow-up until delegate confirmed

------------------------------------------------------------------------

# 18. System Principles

The DSE must ensure:

No duplicate outreach Sponsor contacts are never contacted Only delegate
contacts are targeted All communications are logged Human override is
always possible Calendar availability is respected
