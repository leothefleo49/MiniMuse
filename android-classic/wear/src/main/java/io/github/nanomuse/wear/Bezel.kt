package io.github.nanomuse.wear

import kotlin.math.roundToInt

/** What the rotating bezel (or crown) means: scroll the page, or change the call's volume. */
object Bezel {
    /** Pixels to scroll for one rotary event: clockwise (a negative scroll axis) moves the page down. */
    fun scrollPixels(axis: Float, scrollFactor: Float): Int = (-axis * scrollFactor).roundToInt()

    /**
     * Volume steps for one rotary event, carrying the fraction between events so a slow turn
     * still adds up to a step. Clockwise is louder. Returns (whole steps, the new carry).
     */
    fun volumeSteps(axis: Float, carry: Float): Pair<Int, Float> {
        val total = carry + (-axis)
        val steps = total.toInt()
        return steps to (total - steps)
    }
}
