// Button: every clickable action. Variants:
//   primary   - the one main action on a screen (Promote, Sign)
//   secondary - ordinary actions
//   ghost     - low-emphasis actions in toolbars and tables
//   danger    - destructive or irreversible actions (Revoke, Reset demo data)
// ButtonLink renders the same look as a router link, for navigation.
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Link, type LinkProps } from "react-router-dom";
import { Icon, type IconName } from "./Icon";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
export type ButtonSize = "sm" | "md";

interface Common {
  variant?: ButtonVariant;
  size?: ButtonSize;
  icon?: IconName;
  iconRight?: IconName;
  children?: ReactNode;
}

export interface ButtonProps extends Common, Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children"> {
  /** Shows a spinner and disables the button while an action runs. */
  loading?: boolean;
}

function classes(variant: ButtonVariant, size: ButtonSize, extra?: string, iconOnly?: boolean) {
  return ["btn", `btn-${variant}`, `btn-${size}`, iconOnly ? "btn-icon" : "", extra ?? ""].join(" ").trim();
}

export function Button({ variant = "secondary", size = "md", icon, iconRight, loading, disabled, children, className, type = "button", ...rest }: ButtonProps) {
  const iconOnly = !children && Boolean(icon);
  return (
    <button type={type} className={classes(variant, size, className, iconOnly)} disabled={disabled || loading} aria-busy={loading || undefined} {...rest}>
      {loading ? <Icon name="loader" spin /> : icon ? <Icon name={icon} /> : null}
      {children ? <span>{children}</span> : null}
      {iconRight && !loading ? <Icon name={iconRight} /> : null}
    </button>
  );
}

export interface ButtonLinkProps extends Common, Omit<LinkProps, "children"> {}

export function ButtonLink({ variant = "secondary", size = "md", icon, iconRight, children, className, ...rest }: ButtonLinkProps) {
  return (
    <Link className={classes(variant, size, className, !children && Boolean(icon))} {...rest}>
      {icon ? <Icon name={icon} /> : null}
      {children ? <span>{children}</span> : null}
      {iconRight ? <Icon name={iconRight} /> : null}
    </Link>
  );
}
