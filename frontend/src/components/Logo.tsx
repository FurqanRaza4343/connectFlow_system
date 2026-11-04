export function Logo({ size = 32, text = true }: { size?: number; text?: boolean }) {
  return (
    <span className="logo">
      <img src="/logo.svg" width={size} height={size} alt="" />
      {text && <span className="logo-text">Connect<b>Flow</b></span>}
    </span>
  );
}
