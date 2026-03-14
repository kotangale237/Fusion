from django_cron import CronJobBase, Schedule
from datetime import datetime

class EmailPrinterCron(CronJobBase):
    RUN_EVERY_MINS = 1000  # Runs every 2 minutes

    schedule = Schedule(run_every_mins=RUN_EVERY_MINS)
    code = 'academic_procedures.email_printer_cron'  # Unique code

    def do(self):
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f"Email sent at {now}")

import logging
from datetime import timedelta

from django_cron import CronJobBase, Schedule
from django.utils import timezone

from .models import ThesisSubmission, ReviewInvitation
from .utils import send_invitation_email, send_review_form_email

logger = logging.getLogger(__name__)

class ProcessReviewInvitationsJob(CronJobBase):
    """
    Runs once a day and:
      1) For each in‐review submission, find the lowest‐priority invite
      2) If never sent → send initial invitation
      3) If expired → mark expired, move on
      4) If pending >3d since last_sent → resend invitation
      5) If accepted → send daily review‐form link
    """
    RUN_EVERY_MINS = 1440  # once daily (24 hours)
    schedule = Schedule(run_every_mins=RUN_EVERY_MINS)
    code = 'academic_procedures.process_review_invitations'

    def do(self):
        now = timezone.now()
        submissions = ThesisSubmission.objects.filter(status='in_review')
        logger.info(f"ProcessReviewInvitationsJob starting for {submissions.count()} submissions")

        for sub in submissions:
            # Skip if someone already completed
            if ReviewInvitation.objects.filter(submission=sub, status='completed').exists():
                logger.debug(f"Submission {sub.id} already reviewed; skipping")
                continue

            # Get invites in priority order
            invites = ReviewInvitation.objects.filter(submission=sub).order_by('priority')
            for inv in invites:
                if inv.is_finalized():
                    logger.debug(f"Invitation {inv.token} already finalized ({inv.status}); skipping")
                    continue

                try:
                    # 1) Initial invitation
                    if inv.last_sent is None:
                        inv.last_sent = now
                        # set expires_at once
                        inv.expires_at = now + timedelta(days=30)
                        inv.save(update_fields=['last_sent', 'expires_at'])
                        send_invitation_email(inv)
                        logger.info(f"Sent initial invitation for token {inv.token}")
                        break

                    # 2) Expire after 30 days
                    if inv.is_expired():
                        inv.status = 'expired'
                        inv.save(update_fields=['status'])
                        logger.info(f"Expired invitation {inv.token}")
                        continue

                    # 3) Reminder every 3 days
                    if inv.status == 'pending' and now >= inv.last_sent + timedelta(days=3):
                        send_invitation_email(inv)
                        inv.last_sent = now
                        inv.save(update_fields=['last_sent'])
                        logger.info(f"Sent reminder for token {inv.token}")
                        break

                    # 4) Daily review‐form link once accepted
                    if inv.status == 'accepted' and (inv.review_form_sent is None or now >= inv.review_form_sent + timedelta(days=1)):
                        send_review_form_email(inv)
                        inv.review_form_sent = now
                        inv.save(update_fields=['review_form_sent'])
                        logger.info(f"Sent review‐form link for token {inv.token}")
                        break

                except Exception as e:
                    # Catch errors per‐invitation so one failure doesn't halt the job
                    logger.exception(f"Error processing invitation {inv.token} for submission {sub.id}: {e}")
                    # continue to next invitation or submission
                    continue

        logger.info("ProcessReviewInvitationsJob completed")