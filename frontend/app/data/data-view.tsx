"use client";

import { Database, FileSpreadsheet, FileText, Layers } from "lucide-react";
import { useState } from "react";

import { PageHeader, Tabs } from "@/components/ui";

import { CsvImport } from "./csv-import";
import { DocumentSearch } from "./document-search";
import { DocumentsList } from "./documents-list";
import { PdfIntake } from "./pdf-intake";
import { Templates } from "./templates";
import { WorkspaceAdmin } from "./workspace-admin";

type Tab = "csv" | "pdf" | "templates" | "workspace";

export function DataView() {
  const [tab, setTab] = useState<Tab>("csv");
  return (
    <>
      <PageHeader
        title="Data hub"
        description="Bring your network in from ERP extracts (CSV) and turn supplier notices and risk bulletins (PDF) into disruptions on the graph."
        actions={
          <Tabs<Tab>
            value={tab}
            onChange={setTab}
            tabs={[
              { id: "csv", label: "CSV import", icon: <FileSpreadsheet className="size-4" /> },
              { id: "pdf", label: "Risk documents", icon: <FileText className="size-4" /> },
              { id: "templates", label: "Templates", icon: <Layers className="size-4" /> },
              { id: "workspace", label: "Workspace", icon: <Database className="size-4" /> },
            ]}
          />
        }
      />
      {tab === "csv" && <CsvImport />}
      {tab === "pdf" && (
        <div className="space-y-6">
          <PdfIntake />
          <DocumentSearch />
          <DocumentsList />
        </div>
      )}
      {tab === "templates" && <Templates />}
      {tab === "workspace" && <WorkspaceAdmin />}
    </>
  );
}
