import { FC } from "react";
import { Spinner } from "@decky/ui";
import type { LoadingState, ResultMessage } from "./types";

export const InlineStatus: FC<{ loading: LoadingState; result: ResultMessage | null; section: string }> = ({
  loading,
  result,
  section,
}) => {
  if (loading.active === section) {
    return (
      <div style={{ display: "flex", alignItems: "center", gap: "8px", padding: "4px 0 8px 0" }}>
        <Spinner style={{ width: "16px", height: "16px" }} />
        <span style={{ fontSize: "12px", color: "#aaa" }}>{loading.message}</span>
      </div>
    );
  }
  if (result && result.key === section) {
    return (
      <div
        style={{
          padding: "4px 0 8px 0",
          fontSize: "12px",
          color: result.type === "error" ? "#ff4444" : "#44bb44",
        }}
      >
        {result.text}
      </div>
    );
  }
  return null;
};
