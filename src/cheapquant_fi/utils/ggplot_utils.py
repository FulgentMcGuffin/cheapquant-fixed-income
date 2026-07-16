import pandas as pd
import numpy as np
from plotnine import ggplot, aes, geom_tile, geom_text, theme_minimal, labs, theme, element_text
from sklearn.datasets import make_regression

def plot_correlation_matrix() -> ggplot:
    # Create sample data if you don't have your own DataFrame
    np.random.seed(42)    
    X, y = make_regression(n_samples=100, n_features=5, noise=0.1)    
    df = pd.DataFrame(X, columns=[f'Feature_{i}' for i in range(1, 6)])
    df['Target'] = y

    # Calculate correlation matrix
    corr_matrix = df.corr()

    # Melt the correlation matrix for plotting
    corr_melted = corr_matrix.reset_index().melt(id_vars='index')
    corr_melted.columns = ['Var1', 'Var2', 'Correlation']

    # Create the heatmap
    heatmap = (
        ggplot(corr_melted, aes(x='Var2', y='Var1', fill='Correlation')) +
        geom_tile() +
        geom_text(aes(label=lambda x: f"{x:.2f}"), size=10) +
        theme_minimal() +
        labs(title='Correlation Matrix Heatmap',
            x='Variables',
            y='Variables') +
        theme(figure_size=(8, 6),
            axis_text_x=element_text(rotation=45, ha="right"))
    )
    print(heatmap)
    
if __name__ == "__main__":
    plot_correlation_matrix()
