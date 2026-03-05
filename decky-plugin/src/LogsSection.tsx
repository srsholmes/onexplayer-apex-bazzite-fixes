import { useState, useEffect, useRef, FC } from "react";
import {
  ButtonItem,
  PanelSection,
  PanelSectionRow,
} from "@decky/ui";
import type { LoadingState, ResultMessage } from "./types";
import { getLogs, saveLogs } from "./rpc";
import { InlineStatus } from "./InlineStatus";

export const LogsSection: FC<{
  loading: LoadingState;
  setLoading: (l: LoadingState) => void;
  showResult: (key: string, text: string, type: "success" | "error") => void;
  result: ResultMessage | null;
}> = ({ loading, setLoading, showResult, result }) => {
  const [showLogs, setShowLogs] = useState(false);
  const [logLines, setLogLines] = useState<string[]>([]);
  const logEndRef = useRef<HTMLDivElement>(null);

  // Poll logs when expanded
  useEffect(() => {
    if (!showLogs) return;
    const fetchLogs = async () => {
      try {
        const res = await getLogs(30);
        setLogLines(res.lines);
      } catch (_) {
        // Log fetch failed — will retry on next interval
      }
    };
    fetchLogs();
    const interval = setInterval(fetchLogs, 2000);
    return () => clearInterval(interval);
  }, [showLogs]);

  // Auto-scroll logs to bottom
  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logLines]);

  return (
    <PanelSection title="Logs">
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={() => setShowLogs((prev) => !prev)}>
          {showLogs ? "Hide Logs" : "Show Logs"}
        </ButtonItem>
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          onClick={async () => {
            setLoading({ active: "saveLogs", message: "Saving logs..." });
            try {
              const res = await saveLogs();
              if (res.success) {
                showResult("saveLogs", `Saved to ${res.path}`, "success");
              } else {
                showResult("saveLogs", res.error || "Failed to save", "error");
              }
            } catch (e) {
              showResult("saveLogs", `Error: ${e}`, "error");
            } finally {
              setLoading({ active: null, message: "" });
            }
          }}
          disabled={loading.active === "saveLogs"}
        >
          Save Logs to Downloads
        </ButtonItem>
      </PanelSectionRow>
      <InlineStatus loading={loading} result={result} section="saveLogs" />
      {showLogs && (
        <PanelSectionRow>
          <div
            style={{
              backgroundColor: "#1a1a1a",
              border: "1px solid #333",
              borderRadius: "4px",
              padding: "8px",
              maxHeight: "200px",
              overflowY: "auto",
              fontFamily: "monospace",
              fontSize: "10px",
              lineHeight: "1.4",
              color: "#ccc",
              whiteSpace: "pre-wrap",
              wordBreak: "break-all",
            }}
          >
            {logLines.length === 0 ? (
              <span style={{ color: "#666" }}>No log entries yet</span>
            ) : (
              logLines.map((line, i) => (
                <div key={i}>{line}</div>
              ))
            )}
            <div ref={logEndRef} />
          </div>
        </PanelSectionRow>
      )}
    </PanelSection>
  );
};
