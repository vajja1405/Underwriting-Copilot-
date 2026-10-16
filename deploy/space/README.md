---
title: Fundproof
emoji: 🧾
colorFrom: green
colorTo: gray
sdk: docker
app_port: 7860
hf_oauth: true
pinned: false
short_description: "Prototype: loan-file numbers with their evidence status"
---

# Fundproof (prototype)

> **Educational planning tool. Not financial, legal or lending advice. Lenders make their own decisions.**

Fundproof is a demonstration project by Bhavani Adula and Rahul Vajja. It puts two sides of a small-business loan in
one web app, and its core idea is that every number carries its evidence status (verified, stated by the user, an
assumption, a model estimate, conflicting, or unknown):

- **Founder Studio**: for someone planning a small business and preparing to talk to a lender.
- **Lender Desk**: for a loan reviewer. The copilot never decides; a person does.

## Status

This is a prototype. Sections that are not built yet say so on the page. There are no payments of any kind.

## Data

- Demo files are **SYNTHETIC**: generated, not real people or businesses, and labeled SYNTHETIC wherever they are shown.
- Please do not enter real personal or financial information. The prototype is not set up for it yet.

## Sign-in

Sign in with your Hugging Face account. The app asks only for the `openid` and `profile` scopes, so it sees your
username and display name. If sign-in does not work inside this page's frame (some browsers block cookies in
embedded pages), open the app in its own tab.

## Source

- Code and documentation: <https://github.com/vajja1405/Underwriting-Copilot->
- How this Space is built and deployed: `docs/deployment.md` in that repository.
- This Space is updated only by the repository's deploy workflow; edits made here directly are overwritten.

No license has been chosen for this project yet; that is the owners' decision.
