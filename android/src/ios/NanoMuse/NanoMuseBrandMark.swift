//
//  NanoMuseBrandMark.swift
//  nanoMuse
//
//  The app icon as a view, for the pages that stand for the app rather than
//  for the agent (docs/brand.md): the sign-in page of the first run. The
//  tile is the white squircle with the hairline edge and the MM mark in the
//  brand gradient — drawn from the same path as `assets/brand/nanomuse-mark.svg`
//  (tile coordinates, viewBox 0 0 100 100), so it needs no asset and matches
//  the icon on the home screen. The face stays the agent's; it is never used
//  where the app is meant.
//

import SwiftUI

/// The separated bold MM strokes. Geometry synced from `nanomuse-mark.svg`, in a 100 × 100 tile.
struct NanoMuseMarkShape: Shape {
    func path(in rect: CGRect) -> Path {
        let s = min(rect.width, rect.height) / 100
        let ox = rect.minX + (rect.width - 100 * s) / 2
        let oy = rect.minY + (rect.height - 100 * s) / 2
        func p(_ x: CGFloat, _ y: CGFloat) -> CGPoint { CGPoint(x: ox + x * s, y: oy + y * s) }
        var path = Path()
        // Left M
        path.move(to: p(14.5, 70))
        path.addLine(to: p(14.5, 30))
        path.addLine(to: p(28.5, 57))
        path.addLine(to: p(42.5, 30))
        path.addLine(to: p(42.5, 70))
        // Right M
        path.move(to: p(57.5, 70))
        path.addLine(to: p(57.5, 30))
        path.addLine(to: p(71.5, 57))
        path.addLine(to: p(85.5, 30))
        path.addLine(to: p(85.5, 70))
        return path
    }
}

/// The icon tile: white squircle (27 % radius), hairline edge, the mark in the brand gradient.
struct NanoMuseBrandMark: View {
    var size: CGFloat = 72

    private var tile: RoundedRectangle {
        RoundedRectangle(cornerRadius: size * 0.27, style: .continuous)
    }

    var body: some View {
        ZStack {
            tile.fill(Color.white)
            NanoMuseMarkShape()
                .stroke(
                    LinearGradient(
                        colors: [Color(red: 0x01 / 255, green: 0x5C / 255, blue: 0xFB / 255), Color(red: 0x01 / 255, green: 0x86 / 255, blue: 0xFB / 255)],
                        startPoint: .leading,
                        endPoint: .trailing
                    ),
                    style: StrokeStyle(lineWidth: size * 0.085, lineCap: .round, lineJoin: .round)
                )
        }
        .frame(width: size, height: size)
        .overlay(tile.stroke(Color(red: 0xD9 / 255, green: 0xD9 / 255, blue: 0xDE / 255), lineWidth: 1))
        .accessibilityHidden(true)
    }
}
