import { androidApp } from "../android";
import { useT } from "../i18n";
import { ConfirmDialog } from "./ConfirmDialog";

/**
 * "Switch mode" on the phone drops the connection and goes back to the start screen, where the
 * person picks this phone or a computer again. It used to do that the moment it was tapped, with
 * no word about it. This says what happens first, and is what every switch button asks.
 */
export function SwitchModeConfirm({ open, onCancel }: { open: boolean; onCancel: () => void }) {
  const t = useT();
  return (
    <ConfirmDialog
      open={open}
      title={t("Switch between phone and computer?")}
      body={`${t("MiniMuse will close and go back to its start screen, where you choose to run on this phone or connect to a computer.")}\n\n${t("Nothing is deleted: your chats and settings stay on the phone and on the computer. While you switch, this phone stops running MiniMuse in the background and notifications pause until you choose again.")}`}
      confirmLabel={t("Switch mode")}
      onConfirm={() => {
        onCancel();
        androidApp()?.disconnect();
      }}
      onCancel={onCancel}
    />
  );
}
