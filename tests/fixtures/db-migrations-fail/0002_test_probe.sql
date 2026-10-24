-- TEST-ONLY expand migration (never part of a product release).
CREATE TABLE "wd_migration_probe" (
	"id" integer PRIMARY KEY NOT NULL,
	"note" text DEFAULT '' NOT NULL
);
--> statement-breakpoint
ALTER TABLE "desktop_settings" ADD COLUMN "probe_note" text;
--> statement-breakpoint
CREATE INDEX "fs_nodes_parent_id_probe_idx" ON "fs_nodes" USING btree ("parent_id");
