-- TEST-ONLY failing migration: valid first statement, then a missing table.
ALTER TABLE "desktop_settings" ADD COLUMN "probe_fail" text;
--> statement-breakpoint
CREATE INDEX "probe_fail_idx" ON "wd_no_such_table" USING btree ("id");
