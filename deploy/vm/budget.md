# Billing budget alert (you do this once, in the console)

A budget alert warns you by email; it does not stop spending.

1. Open <https://console.cloud.google.com/billing> and pick the billing account that pays for your project.
2. Budgets and alerts, then Create budget.
3. Scope: Projects, select your project. Leave services on All services.
4. Amount: a monthly budget, for example 100 USD (the VM, disk and address are about 110 to 130 USD a month if left running;
   see README.md). Pick the figure you are comfortable with.
5. Thresholds: 50%, 90% and 100% of actual spend, plus 100% of forecasted spend. Keep email notifications to billing admins on.
6. Save.

CLI alternative (needs the Billing Budgets API and the billing account id):

```bash
gcloud billing accounts list
gcloud billing budgets create --billing-account=<ACCOUNT_ID> --display-name=robode-studio \
  --budget-amount=100USD --filter-projects=projects/$PROJECT \
  --threshold-rule=percent=0.5 --threshold-rule=percent=0.9 --threshold-rule=percent=1.0
```

If credits cover part of the spend, the budget can be set to include or ignore credits; check which option you chose.
